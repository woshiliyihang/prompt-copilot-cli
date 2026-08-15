from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from types import SimpleNamespace


ROOT = Path.home() / ".prompt-copilot"
ROOT.mkdir(parents=True, exist_ok=True)

LAST_MODEL_CALL_COMPLETED_AT: float | None = None
TOTAL_TOKEN_USAGE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def wait_for_model_call_interval() -> None:
    global LAST_MODEL_CALL_COMPLETED_AT
    if LAST_MODEL_CALL_COMPLETED_AT is None:
        return

    elapsed = time.monotonic() - LAST_MODEL_CALL_COMPLETED_AT
    if elapsed < RE_ACTION_DELAY:
        time.sleep(RE_ACTION_DELAY - elapsed)


def mark_model_call_completed() -> None:
    global LAST_MODEL_CALL_COMPLETED_AT
    LAST_MODEL_CALL_COMPLETED_AT = time.monotonic()


def update_total_token_usage(usage: Any) -> None:
    if usage is None:
        return

    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)):
            TOTAL_TOKEN_USAGE[key] += int(value)


def format_usage_summary(usage: Any) -> str:
    if usage is None:
        return t("token_usage_unavailable")

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    return (
        f"{t('prompt_tokens_label')}={prompt_tokens if prompt_tokens is not None else '-'} | "
        f"{t('completion_tokens_label')}={completion_tokens if completion_tokens is not None else '-'} | "
        f"{t('total_tokens_label')}={total_tokens if total_tokens is not None else '-'}"
    )


def format_cumulative_token_summary() -> str:
    return (
        f"{t('token_usage_label')} "
        f"{t('prompt_tokens_label')}={TOTAL_TOKEN_USAGE['prompt_tokens']} | "
        f"{t('completion_tokens_label')}={TOTAL_TOKEN_USAGE['completion_tokens']} | "
        f"{t('total_tokens_label')}={TOTAL_TOKEN_USAGE['total_tokens']}"
    )


def clear_task_memory_file() -> None:
    MEMORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE_PATH.write_text("# Task Execution Memory\n\n", encoding="utf-8")


def append_task_memory_entry(text: str) -> None:
    MEMORY_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_FILE_PATH.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def summarize_memory_value(value: Any, max_len: int = 240) -> str:
    if isinstance(value, str):
        if len(value) > max_len:
            return value[: max_len - 20] + f"... (truncated, len={len(value)})"
        return value

    if isinstance(value, (dict, list)):
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except Exception:
            serialized = str(value)
        if len(serialized) > max_len:
            return serialized[: max_len - 20] + f"... (truncated, len={len(serialized)})"
        return serialized

    return str(value)


def summarize_tool_result(result: Any) -> str:
    if isinstance(result, dict):
        status = result.get("status")
        content = result.get("content")
        content_summary = summarize_memory_value(content, max_len=200)
        extra_items = [
            f"{k}={summarize_memory_value(v, max_len=80)}"
            for k, v in result.items()
            if k not in {"status", "content"}
        ]
        extra_text = f" | {", ".join(extra_items)}" if extra_items else ""
        return f"status={status}{extra_text}\nContent: {content_summary}"
    return summarize_memory_value(result)


def build_bottom_toolbar_text() -> str:
    base = t("toolbar_help")
    token_summary = format_cumulative_token_summary()
    return f"{token_summary} | {base}"


TRANSLATIONS = {
    "config_field_model": {"zh": "模型名称，例如 qwen2.5-7b-instruct", "en": "Model name, for example qwen2.5-7b-instruct"},
    "config_field_base_url": {"zh": "OpenAI 兼容接口地址，例如 http://127.0.0.1:11434/v1", "en": "OpenAI-compatible base URL, for example http://127.0.0.1:11434/v1"},
    "config_field_api_key": {"zh": "API Key；若是本地模型可填写任意非空字符串", "en": "API key; for a local model you can enter any non-empty string"},
    "config_field_temperature": {"zh": "采样温度，取值通常在 0.0 到 1.0 之间", "en": "Sampling temperature, usually between 0.0 and 1.0"},
    "config_field_debug": {"zh": "是否开启调试日志，建议保持 true", "en": "Whether to enable debug logging; true is recommended"},
    "config_field_mcp": {"zh": "MCP 工具服务器配置对象，包含 enabled 和 servers", "en": "MCP tool-server configuration object containing enabled and servers"},
    "read_file_desc": {"zh": "读取指定文件内容（文本模式）。", "en": "Read the contents of the specified file in text mode."},
    "write_file_desc": {"zh": "写入或覆盖指定文件内容。", "en": "Write or overwrite the content of the specified file."},
    "delete_file_desc": {"zh": "删除指定文件。", "en": "Delete the specified file."},
    "create_directory_desc": {"zh": "创建目录（支持递归创建）。", "en": "Create a directory (supports recursive creation)."},
    "delete_directory_desc": {"zh": "删除目录及其内容。", "en": "Delete a directory and its contents."},
    "rename_path_desc": {"zh": "重命名或移动文件/目录。", "en": "Rename or move a file or directory."},
    "copy_file_desc": {"zh": "复制文件。", "en": "Copy a file."},
    "read_image_as_base64_desc": {"zh": "读取图片文件并将其编码为 base64。", "en": "Read an image file and encode it as base64."},
    "list_dir_desc": {"zh": "列出目录中的文件和子目录。", "en": "List files and subdirectories in the given directory."},
    "search_code_desc": {"zh": "在目录中按文本模式搜索匹配内容。", "en": "Search for matching text within a directory."},
    "edit_file_desc": {"zh": "在文件中替换指定文本。", "en": "Replace a specific string in a file."},
    "path_desc": {"zh": "要读取的文件路径。", "en": "Path of the file to read."},
    "write_path_desc": {"zh": "要写入的目标文件路径。", "en": "Target file path to write to."},
    "content_desc": {"zh": "要写入的文件内容。", "en": "Content to write to the file."},
    "list_dir_path_desc": {"zh": "要列出的目录路径。", "en": "Directory path to list."},
    "list_dir_recursive_desc": {"zh": "是否递归遍历子目录。", "en": "Whether to recursively traverse subdirectories."},
    "interrupt": {"zh": "用户中断", "en": "Interrupted by user"},
    "not_detected": {"zh": "未检测到", "en": "Not detected"},
    "device_environment": {"zh": "设备工程环境：", "en": "Device environment:"},
    "device_time": {"zh": "当前设备时间：", "en": "Current device time:"},
    "device_os": {"zh": "当前操作系统信息：", "en": "Current operating system info:"},
    "device_software": {"zh": "当前设备软件环境信息：", "en": "Current software environment info:"},
    "device_workdir": {"zh": "当前工作目录：", "en": "Current working directory:"},
    "invalid_tool_args": {"zh": "工具参数不是有效 JSON，原始内容：%s", "en": "Tool arguments are not valid JSON. Raw content: %s"},
    "config_header": {"zh": "配置字段说明：", "en": "Configuration field descriptions:"},
    "config_file_read_error": {"zh": "配置文件读取失败，请检查 {path}: {error}", "en": "Failed to read config file. Please check {path}: {error}"},
    "config_file_format_error": {"zh": "配置文件格式不正确，期望 JSON 对象: {path}", "en": "Config file format is invalid. Expected a JSON object: {path}"},
    "config_incomplete": {"zh": "模型配置不完整，首次使用需正确配置模型才能够使用。\n请在 {path} 中完善以下字段：{fields}", "en": "The model configuration is incomplete. Please complete the required fields in {path} before using the agent.\nMissing fields: {fields}"},
    "config_api_key_error": {"zh": "未配置 API Key，请先在 config/model_config.json 中填写 api_key。", "en": "API key is not configured. Please fill in the api_key field in the config file first."},
    "mcp_discover_failed": {"zh": "发现 MCP 工具失败", "en": "Failed to discover MCP tools"},
    "mcp_tool_not_found": {"zh": "未找到 MCP 工具对应的服务配置: {name}", "en": "No MCP server configuration found for tool: {name}"},
    "mcp_tool_unavailable": {"zh": "MCP 工具不可用：{name}，已忽略。", "en": "MCP tool unavailable: {name}; it will be ignored."},
    "mcp_tool_failed": {"zh": "执行 MCP 工具失败", "en": "Failed to execute MCP tool"},
    "tool_missing_arg": {"zh": "{name} 缺少 {arg} 参数。", "en": "{name} is missing the {arg} argument."},
    "unknown_tool": {"zh": "未知工具: {name}", "en": "Unknown tool: {name}"},
    "start_model_call": {"zh": "开始调用模型", "en": "Starting model call"},
    "model_request_params": {"zh": "调试-模型请求参数", "en": "Debug - model request parameters"},
    "model_call_finished": {"zh": "调用模型结束", "en": "Model call finished"},
    "model_raw_response": {"zh": "调试-模型原始返回", "en": "Debug - raw model response"},
    "cancelled": {"zh": "已取消", "en": "Cancelled"},
    "model_cancelled": {"zh": "用户中断了当前模型调用。", "en": "The current model call was interrupted by the user."},
    "model_error": {"zh": "调用模型时出错", "en": "Error while calling the model"},
    "model_quota_error": {"zh": "模型调用失败：检测到配额/限流问题（可能是免费额度已耗尽或未开通付费）。\n请检查配置中的 api_key 与 base_url，或开通/充值服务后重试。", "en": "Model call failed: quota or rate-limit issue detected (the free quota may be exhausted or billing may not be enabled).\nPlease check the api_key and base_url in the config, or enable/top up the service and try again."},
    "model_502_error": {"zh": "模型调用失败：上游模型服务返回了 502（Bad Gateway）或服务端异常。\n这通常是模型服务端暂时不可用、代理转发异常或服务正在重启造成的。\n请稍后重试，并确认 base_url 指向的是可正常提供 /chat/completions 的服务。", "en": "Model call failed: the upstream model service returned 502 (Bad Gateway) or a server-side error.\nThis is usually caused by a temporary outage, proxy forwarding issue, or service restart.\nPlease try again later and confirm that base_url points to a service that serves /chat/completions correctly."},
    "model_call_failed": {"zh": "模型调用失败：{error}", "en": "Model call failed: {error}"},
    "tool_result_title": {"zh": "工具调用结果", "en": "Tool execution result"},
    "config_error_title": {"zh": "配置错误", "en": "Configuration error"},
    "quota_hint": {"zh": "免费额度", "en": "free quota"},
    "tool_execution": {"zh": "开始执行工具：{name}，参数：{args}", "en": "Starting tool execution: {name}, args: {args}"},
    "tool_subprocess_failed": {"zh": "子进程启动失败，cwd=%s，回退到 %s: %s", "en": "Failed to start subprocess in cwd=%s, falling back to %s: %s"},
    "welcome_message": {
        "zh": "输入:\n/exit 退出，\n/clear 清空本地会话，\n/task-start 开始任务上下文，\n/task-end 生成最终提示。\nCtrl+C 可中断当前执行。\n\n启动命令示例：\npython main.py -l zh\npython main.py -t \"你的任务\" -d ./workspace -l en\npython main.py --reset-session\n\n参数说明：\n-t / --task：一次性任务内容\n-d / --workdir：指定工作目录\n-l / --lang：选择语言（zh / en）\n-amc / --agent-messages-count：历史消息数量(推荐默认)\n-rd / --request-delay：请求间隔秒数(推荐默认)\n-hc / --history-count：会话轮次数量(推荐默认)\n--reset-session：重置本地会话记录。",
        "en": "Input:\n/exit to exit,\n/clear to clear the local session,\n/task-start to start a task context,\n/task-end to generate the final prompt.\nCtrl+C can interrupt the current execution.\n\nStartup command examples:\npython main.py -l zh\npython main.py -t \"your task\" -d ./workspace -l en\npython main.py --reset-session\n\nOptions:\n-t / --task: one-off task content\n-d / --workdir: specify the working directory\n-l / --lang: choose language (zh / en)\n-amc / --agent-messages-count: number of messages to keep in agent history (default: 6)\n-rd / --request-delay: delay in seconds between model requests (default: 8)\n-hc / --history-count: number of rounds to keep in conversation history (default: 5)\n--reset-session: reset the local session history."
    },
    "startup_title": {"zh": "启动", "en": "Start"},
    "exit_message": {"zh": "已退出。", "en": "Exited."},
    "bye_message": {"zh": "再见。", "en": "Goodbye."},
    "clear_session_message": {"zh": "会话记录与最近对话记录已清空。", "en": "Session history and recent conversation history have been cleared."},
    "workdir_message": {"zh": "工作目录: {path}", "en": "Working directory: {path}"},
    "cli_config_title": {"zh": "Cli 配置", "en": "CLI configuration"},
    "mcp_connected": {"zh": "已接入 MCP 工具：{count} 个", "en": "Connected MCP tools: {count}"},
    "mcp_config_title": {"zh": "MCP 配置", "en": "MCP configuration"},
    "session_reset_message": {"zh": "会话记录已重置。", "en": "Session history has been reset."},
    "debug_enabled_message": {"zh": "调试模式已开启，会输出模型请求参数与原始返回内容。", "en": "Debug mode is enabled; model request parameters and raw responses will be shown."},
    "debug_config_title": {"zh": "调试配置", "en": "Debug configuration"},
    "task_cancelled": {"zh": "已取消当前任务。", "en": "The current task has been cancelled."},
    "interrupted_title": {"zh": "已中断", "en": "Interrupted"},
    "runtime_error_title": {"zh": "运行时错误", "en": "Runtime error"},
    "tool_execution_error": {"zh": "工具执行错误", "en": "Tool execution error"},
    "task_end_error": {"zh": "Task End 错误", "en": "Task End error"},
    "task_end_notice": {"zh": "Task End 提示", "en": "Task End notice"},
    "task_end_file_missing": {"zh": "未找到对话记录文件：{path}", "en": "Conversation history file not found: {path}"},
    "task_end_empty": {"zh": "对话记录为空或格式不正确。", "en": "Conversation history is empty or malformed."},
    "task_end_no_task_start": {"zh": "未在 recent_conversations.md 中找到包含 /task-start 的轮次。", "en": "No round containing /task-start was found in recent_conversations.md."},
    "task_end_generate_failed": {"zh": "生成失败", "en": "Generation failed"},
    "task_end_no_prompt": {"zh": "模型未返回任何最终提示。", "en": "The model did not return a final prompt."},
    "task_end_result": {"zh": "生成结果", "en": "Generation result"},
    "task_end_completed": {"zh": "已生成最终提示并写入: {path}", "en": "Final prompt generated and written to: {path}"},
    "task_end_done": {"zh": "Task End 完成", "en": "Task End complete"},
    "starting_tool_call": {"zh": "开始调用工具", "en": "Starting tool call"},
    "current_tool_cancelled": {"zh": "已取消当前工具执行。", "en": "The current tool execution has been cancelled."},
    "cli_parser_description": {"zh": "Prompt Copilot CLI 编程 Agent", "en": "Prompt Copilot CLI coding agent"},
    "task_argument_help": {"zh": "一次性任务内容，适合单次执行。", "en": "One-off task content, suitable for a single run."},
    "workdir_argument_help": {"zh": "工作目录。", "en": "Working directory."},
    "reset_session_help": {"zh": "重置本地持久化会话记录。", "en": "Reset local persisted session history."},
    "prompt_placeholder": {"zh": "你想让我做什么？> ", "en": "What do you want me to do? > "},
    "toolbar_help": {"zh": "可用命令: /exit /clear /task-start /task-end  | Tab 补全 | Ctrl+C 可中断当前执行", "en": "Available commands: /exit /clear /task-start /task-end | Tab completion | Ctrl+C can interrupt the current execution"},
    "token_usage_label": {"zh": "累计 token", "en": "Cumulative tokens"},
    "prompt_tokens_label": {"zh": "prompt", "en": "prompt"},
    "completion_tokens_label": {"zh": "completion", "en": "completion"},
    "total_tokens_label": {"zh": "total", "en": "total"},
    "token_usage_unavailable": {"zh": "无 token 使用信息", "en": "No token usage information"},
    "context_size_label": {"zh": "提交上下文大小", "en": "Submitted context size"},
    "response_length_label": {"zh": "回复内容长度", "en": "Response content length"},
    "recent_conversations_header": {"zh": "# 最近对话记录", "en": "# Recent conversations"},
    "task_end_generation_failed_log": {"zh": "调用模型生成最终提示失败", "en": "Failed to generate the final prompt with the model"},
    "final_prompt_header": {"zh": "# 最终提示词", "en": "# Final prompt"},
}


def t(key: str, **kwargs: Any) -> str:
    mapping = TRANSLATIONS.get(key, {})
    text = mapping.get(UI_SYSTEM_LANGUAGE, mapping.get("en", key))
    if kwargs:
        return text.format(**kwargs)
    return text

MEMORY_FILE_PATH = ROOT / "memory.md"

DEFAULT_SYSTEM_PROMPT = f"""
你是一个基于 CLI 的编程 Agent，你的名字是 Jason Li，专注于使用文件、命令、Python 脚本等工具完成开发任务。

工作原则：
1、先检查工作目录并理解需求。
2、优先使用网络搜索类工具搜索网络实时信息，例如：天气、新闻、资讯等等。
3、所有代码文件生成、编辑、删除等操作均在工作目录中执行。如果用户没有指定具体目录，默认工作目录为工程的根目录。
4、如果需要，输出简洁的说明与下一步建议。
5、若涉及删除系统文件或执行危险命令，必须先向用户确认后方可执行。
6、当用户输入指令：/task-start 的时候,直接回复：请输入你的第一条初始提示。
7、遇到信息盲区时，严禁主观臆测，请优先使用搜索工具补齐信息缺口，确保回答精准有据。
8、在任务处理中，工具调用会产生大量中间信息，而对话只保存有限几轮上下文,在生成最终结果或者是某些步骤需要回溯前面缺失的历史信息的时候可以读取记忆文件:{MEMORY_FILE_PATH}。
9、如果用户想“读取/识别其他二进制文件”，你必须明确告知：我无法直接读取或理解通用二进制文件内容。
10、如果用户想“查看图片内容”，应优先调用图片读取工具。
11、当用户输入一条任务指令时，如果是详细的任务清单，你就理解用户要求逐步完成工作，同时执行每个步骤的时候明确说明当前环节与进度，实时反馈任务状态。
12、使用 execute_python_script 时必须在脚本内自行管理进程生命周期：若是 ls、pytest、build 等短命令用 subprocess.run 显式设置 timeout，若是 npm run dev、flask run 等常驻服务用 subprocess.Popen 脱离终端启动并在脚本内轮询健康检查 URL，确认就绪后打印 PID 并立即 sys.exit() 退出以防触发 360 秒强制超时。
13、无法调用官方联网检索工具而通过脚本抓取网络内容时需区分数据类型处理，抓取普通网页文本需剔除 HTML 标签、样式代码、广告碎片、无效注释、多余空行等冗余内容，仅留存有效正文且文本超出 8000 字符则截断，文本输出上限为 8000 字符，抓取程序源代码则无需过滤，完整保留原始代码、自带注释、缩进换行与原有格式，代码输出无字符数量限制。
14、脱离终端启动常驻服务时务必兼顾跨平台兼容：Linux/Mac 环境设置 start_new_session=True，Windows 环境设置 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP。
15、凡耗时不可控的超长任务（如大文件下载、模型训练、全量测试、复杂构建等），严禁在脚本内同步阻塞，必须用 subprocess.Popen 跨平台脱离终端启动并将进度心跳写入本地日志文件，打印 PID 后立即 sys.exit()，工具 timeout_seconds 设为 15 秒仅验证启动；后续须通过检查日志监控进度，若发现任务完成或长时间无心跳更新，必须主动调用系统命令（如 kill -9 PID）清理后台进程以防孤儿进程耗尽资源；仅在任务自身支持超时参数或耗时可预估时，方可在脚本内同步执行并设合理 timeout_seconds。
16、如果需要搜索互联网信息时候优先使用中国国内搜索引擎优先，例如：bing、百度等。

"""

UI_SYSTEM_LANGUAGE = "en"
APPLICATION_VERSION = "0.2.9"
WORKSPACE_DIR = ROOT / "workspace"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "agent_runtime.log"
DEFAULT_MAX_CHAT_COUNT = 6
CHAT_MESSAGE_MAX_COUNT = 8
CONFIG_SAVE_FILE_PATH = ROOT / "config.json"
RE_ACTION_DELAY = 1 # unit: seconds
TOOL_SUBPROCESS_TIMEOUT = 6 * 60  # 1 hour in seconds
MODEL_REQUEST_TIMEOUT_SECONDS = 600  # generous timeout for slower model generations
TASK_DESCRIPTION_TARGET = "[This is the task list after understanding the user's needs]"
DEFAULT_MODEL_CONFIG: dict[str, Any] = {
    "model": "",
    "base_url": "",
    "api_key": "",
    "temperature": 0.2,
    "debug": False,
    "mcp": {
        "enabled": True,
        "servers": [
            # {
            #     "name": "bing",
            #     "command": "npx",
            #     "args": ["-y", "bing-cn-mcp"]
            # },
            # {
            #     "name": "open-websearch-http",
            #     "transport": "http",
            #     "url": "http://127.0.0.1:3000/mcp"
            # }
        ]
    }
}


CONFIG_FIELD_DESCRIPTIONS = {
    "model": t("config_field_model"),
    "base_url": t("config_field_base_url"),
    "api_key": t("config_field_api_key"),
    "temperature": t("config_field_temperature"),
    "debug": t("config_field_debug"),
    "mcp": t("config_field_mcp"),
}

console = Console()

logger = logging.getLogger("cli_agent")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": t("read_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": t("write_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("write_path_desc")},
                    "content": {"type": "string", "description": t("content_desc")},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": t("delete_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": t("create_directory_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": t("delete_directory_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_path",
            "description": t("rename_path_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": t("path_desc")},
                    "new_path": {"type": "string", "description": t("write_path_desc")}
                },
                "required": ["old_path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": t("copy_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": t("path_desc")},
                    "destination_path": {"type": "string", "description": t("write_path_desc")}
                },
                "required": ["source_path", "destination_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_image_as_base64",
            "description": t("read_image_as_base64_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("path_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": t("list_dir_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("list_dir_path_desc")},
                    "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": t("search_code_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("list_dir_path_desc")},
                    "pattern": {"type": "string", "description": "Text pattern to search for."},
                    "recursive": {"type": "boolean", "description": t("list_dir_recursive_desc")},
                    "max_results": {"type": "integer", "description": "Maximum number of matches to return."}
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": t("edit_file_desc"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": t("write_path_desc")},
                    "old_string": {"type": "string", "description": "The text to replace."},
                    "new_string": {"type": "string", "description": "The replacement text."}
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_script",
            "description": "Execute a Python script or a block of Python code. Use the 'timeout_seconds' parameter to control the maximum execution time and prevent the process from hanging indefinitely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The Python script content to execute."
                    },
                    "cwd": {
                        "type": "string",
                        "description": "The working directory for the script execution."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Maximum execution time in seconds before the process is terminated. Defaults to 120. Set a shorter timeout for quick commands to avoid waiting."
                    }
                },
                "required": ["script", "cwd"]
            }
        }
    }
]
ACTIVE_MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_CONFIG: dict[str, Any] = {}
ACTIVE_MCP_TOOL_CONFIGS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_SERVER_BY_NAME: dict[str, dict[str, Any]] = {}
INTERRUPTION_REQUESTED = False


def handle_sigint(signum: int, frame: Any) -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = True
    raise KeyboardInterrupt(t("interrupt"))


def ensure_not_interrupted() -> None:
    if INTERRUPTION_REQUESTED:
        raise KeyboardInterrupt(t("interrupt"))


def reset_interruption_state() -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = False


class SessionStore:
    def __init__(self, session_file: Path, max_messages: int = DEFAULT_MAX_CHAT_COUNT):
        self.session_file = session_file
        self.max_messages = max_messages
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.session_file.exists():
            self.session_file.write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

    def load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def save(self, history: list[dict[str, Any]]) -> None:
        if len(history) > self.max_messages:
            history = history[-self.max_messages:]
        self.session_file.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")


class SlashCommandCompleter(Completer):
    def __init__(self, commands: list[str]):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return

        prefix = text
        if " " in text:
            return

        for command in self.commands:
            if command.startswith(prefix):
                yield Completion(command, start_position=-len(prefix))


def get_version_from_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
        )
        stdout = result.stdout or ''
        output = stdout.strip().splitlines()[0] if stdout.strip() else ''
        return output or t("not_detected")
    except Exception:
        return t("not_detected")


def build_device_environment_context(workdir: str) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os_name = platform.system() or t("unknown") if False else platform.system() or 'Unknown'
    os_release = platform.release() or 'Unknown'
    os_version = platform.version() or 'Unknown'
    os_arch = platform.machine() or 'Unknown'
    python_version = platform.python_version() or sys.version.split()[0]
    node_path = shutil.which('node')
    node_version = get_version_from_command(['node', '--version']) if node_path else t("not_detected")
    npm_version = get_version_from_command(['npm', '--version']) if shutil.which('npm') else t("not_detected")

    return (
        t("device_environment") + "\n"
        + t("device_time") + now + "\n"
        + t("device_os")
        + f"system={os_name}, release={os_release}, version={os_version}, arch={os_arch}\n"
        + t("device_software")
        + f"python={python_version}, node={node_version}, npm={npm_version}\n"
        + t("device_workdir") + workdir
    )


def safe_parse_tool_args(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning(t("invalid_tool_args"), raw_arguments)
            return {}
    return {}


def build_multimodal_user_message(
    text: str,
    image_path: str | os.PathLike[str],
    max_bytes: int | None = None,
) -> dict[str, Any]:
    path = Path(image_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_bytes = path.read_bytes()
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"

    if max_bytes is None:
        max_bytes = 120_000

    if max_bytes and len(image_bytes) > max_bytes:
        try:
            import io
            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGB")
                candidate_bytes = image_bytes
                candidate_mime = mime_type
                width, height = img.size
                for scale in [1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.03, 0.01]:
                    resized = img.resize((max(16, int(width * scale)), max(16, int(height * scale))), Image.Resampling.LANCZOS)
                    for quality in [85, 70, 55, 40, 25, 15, 10]:
                        buffer = io.BytesIO()
                        resized.save(buffer, format="JPEG", quality=quality, optimize=True)
                        candidate_bytes = buffer.getvalue()
                        candidate_mime = "image/jpeg"
                        encoded_candidate = base64.b64encode(candidate_bytes).decode("ascii")
                        if len(encoded_candidate) <= max_bytes:
                            image_bytes = candidate_bytes
                            mime_type = candidate_mime
                            break
                    if len(base64.b64encode(candidate_bytes).decode("ascii")) <= max_bytes:
                        break
                else:
                    tiny = img.resize((32, 32), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    tiny.save(buffer, format="JPEG", quality=15, optimize=True)
                    image_bytes = buffer.getvalue()
                    mime_type = "image/jpeg"
                    if len(base64.b64encode(image_bytes).decode("ascii")) > max_bytes:
                        image_bytes = b"\x00"
                        mime_type = "image/jpeg"
        except Exception:
            image_bytes = image_bytes[:max(1, max_bytes // 2)]

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded}",
                },
            },
        ],
    }




def resolve_execution_cwd(cwd: Any, fallback: str | os.PathLike[str] | None = None) -> str:
    fallback_path = Path(fallback or Path.cwd()).expanduser()
    if cwd in (None, "", "."):
        return str(fallback_path)

    candidate = Path(str(cwd)).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve(strict=False)
    except Exception:
        return str(fallback_path)

    if resolved.exists() and resolved.is_dir():
        return str(resolved)

    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return str(resolved)
    except Exception:
        return str(fallback_path)


def _format_config_field_help() -> str:
    lines = [t("config_header")]
    for field_name, description in CONFIG_FIELD_DESCRIPTIONS.items():
        lines.append(f"- {field_name}: {description}")
    return "\n".join(lines)


def ensure_config(workdir: Path) -> tuple[dict[str, Any], str]:
    CONFIG_SAVE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_SAVE_FILE_PATH.exists():
        default_payload = {
            key: (value.copy() if isinstance(value, dict) else value)
            for key, value in DEFAULT_MODEL_CONFIG.items()
        }
        CONFIG_SAVE_FILE_PATH.write_text(
            json.dumps(default_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    try:
        file_payload = json.loads(CONFIG_SAVE_FILE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            t("config_file_read_error", path=CONFIG_SAVE_FILE_PATH, error=exc) + "\n\n"
            + f"{_format_config_field_help()}"
        ) from exc

    if not isinstance(file_payload, dict):
        raise RuntimeError(
            t("config_file_format_error", path=CONFIG_SAVE_FILE_PATH) + "\n\n"
            + f"{_format_config_field_help()}"
        )

    for key, value in file_payload.items():
        if isinstance(value, dict) and isinstance(DEFAULT_MODEL_CONFIG.get(key), dict):
            DEFAULT_MODEL_CONFIG[key] = value
        else:
            DEFAULT_MODEL_CONFIG[key] = value

    required_fields = ["model", "base_url", "api_key"]
    missing_fields = [field for field in required_fields if not str(DEFAULT_MODEL_CONFIG.get(field, "")).strip()]
    if missing_fields:
        raise RuntimeError(
            t("config_incomplete", path=CONFIG_SAVE_FILE_PATH, fields=', '.join(missing_fields)) + "\n\n"
            + f"{_format_config_field_help()}"
        )

    device_prompt = build_device_environment_context(str(workdir))
    system_prompt = device_prompt + "\n\n" + DEFAULT_SYSTEM_PROMPT
    return DEFAULT_MODEL_CONFIG, system_prompt


def build_client(model_cfg: dict[str, Any]) -> OpenAI:
    api_key = model_cfg.get("api_key")
    if not api_key:
        raise RuntimeError(t("config_api_key_error"))
    base_url = model_cfg.get("base_url")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    kwargs["timeout"] = MODEL_REQUEST_TIMEOUT_SECONDS
    return OpenAI(**kwargs)


def normalize_mcp_tool_definition(tool_obj: Any) -> dict[str, Any]:
    schema = getattr(tool_obj, "inputSchema", None) or {}
    parameters = dict(schema)
    if not parameters.get("type"):
        parameters["type"] = "object"
    if parameters.get("type") == "object" and not parameters.get("properties"):
        parameters["properties"] = {}

    return {
        "type": "function",
        "function": {
            "name": getattr(tool_obj, "name", ""),
            "description": getattr(tool_obj, "description", "") or "",
            "parameters": parameters,
        },
    }


def get_tool_description(tool_call: Any) -> str:
    description = getattr(getattr(tool_call, "function", None), "description", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    tool_name = getattr(getattr(tool_call, "function", None), "name", None)
    if not isinstance(tool_name, str) or not tool_name:
        return ""

    for definition in TOOL_DEFINITIONS + ACTIVE_MCP_TOOL_DEFINITIONS:
        function = definition.get("function") or {}
        if function.get("name") == tool_name:
            return str(function.get("description", "")).strip()

    return ""


async def _run_mcp_session(server_config: dict[str, Any], handler: Any) -> Any:
    transport = str(server_config.get("transport") or server_config.get("type") or "stdio").lower()
    if transport in {"http", "streamable_http", "streamable-http"}:
        url = server_config.get("url")
        headers = server_config.get("headers") or {}
        if not url:
            raise RuntimeError("MCP HTTP server is missing a URL")
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await handler(session)

    if transport == "sse":
        url = server_config.get("url")
        headers = server_config.get("headers") or {}
        if not url:
            raise RuntimeError("MCP SSE server is missing a URL")
        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await handler(session)

    command = server_config.get("command") or "npx"
    args = list(server_config.get("args") or ["-y", "bing-cn-mcp"])
    server_params = StdioServerParameters(command=command, args=args)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await handler(session)


def normalize_mcp_server_config(raw_config: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(fallback or {})
    if isinstance(raw_config, dict):
        merged = dict(base)
        merged.update(raw_config)
        transport = str(merged.get("transport") or merged.get("type") or base.get("transport") or "stdio").lower()
        if transport in {"sse", "http", "streamable_http", "streamable-http"}:
            headers = merged.get("headers") or base.get("headers") or {}
            if isinstance(headers, dict):
                headers = dict(headers)
            else:
                headers = {}
            url = merged.get("url") or base.get("url") or ""
            return {
                "name": merged.get("name") or url or f"mcp-{transport}",
                "transport": transport,
                "url": url,
                "headers": headers,
            }

        command = merged.get("command") or base.get("command") or "npx"
        args_value = merged.get("args") or merged.get("arguments") or base.get("args") or ["-y", "bing-cn-mcp"]
        if isinstance(args_value, str):
            args = [args_value]
        elif isinstance(args_value, list):
            args = list(args_value)
        else:
            args = [str(args_value)]
        return {
            "name": merged.get("name") or f"{command}:{' '.join(args)}",
            "transport": transport,
            "command": command,
            "args": args,
        }

    if isinstance(raw_config, str):
        return {"name": raw_config, "transport": "stdio", "command": raw_config, "args": []}

    return {"name": "mcp", "transport": "stdio", "command": "npx", "args": ["-y", "bing-cn-mcp"]}


def normalize_mcp_server_configs(mcp_cfg: Any) -> list[dict[str, Any]]:
    if isinstance(mcp_cfg, list):
        return [normalize_mcp_server_config(item) for item in mcp_cfg if item is not None]

    if isinstance(mcp_cfg, dict):
        if isinstance(mcp_cfg.get("servers"), list):
            fallback = {
                "command": mcp_cfg.get("command"),
                "args": mcp_cfg.get("args"),
                "transport": mcp_cfg.get("transport") or mcp_cfg.get("type"),
                "url": mcp_cfg.get("url"),
                "headers": mcp_cfg.get("headers"),
            }
            return [normalize_mcp_server_config(item, fallback=fallback) for item in mcp_cfg["servers"] if item is not None]
        return [normalize_mcp_server_config(mcp_cfg)]

    return []


def discover_mcp_tools(model_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    global ACTIVE_MCP_TOOL_DEFINITIONS, ACTIVE_MCP_TOOL_CONFIG, ACTIVE_MCP_TOOL_CONFIGS, ACTIVE_MCP_TOOL_SERVER_BY_NAME

    mcp_cfg = model_cfg.get("mcp", {})
    if isinstance(mcp_cfg, dict):
        enabled = bool(mcp_cfg.get("enabled", True))
    else:
        enabled = True
    if not enabled:
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []

    server_configs = normalize_mcp_server_configs(mcp_cfg)
    if not server_configs:
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []

    ACTIVE_MCP_TOOL_CONFIGS = []
    ACTIVE_MCP_TOOL_CONFIG = {}
    ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
    ACTIVE_MCP_TOOL_DEFINITIONS = []

    async def _discover_one(server_config: dict[str, Any]) -> list[dict[str, Any]]:
        async def _handler(session: ClientSession) -> list[dict[str, Any]]:
            tools = await session.list_tools()
            return [normalize_mcp_tool_definition(tool) for tool in tools.tools]

        return await _run_mcp_session(server_config, _handler)

    try:
        definitions: list[dict[str, Any]] = []
        active_configs: list[dict[str, Any]] = []
        for server_config in server_configs:
            try:
                discovered = asyncio.run(_discover_one(server_config))
                definitions.extend(discovered)
                active_configs.append(server_config)
                for item in discovered:
                    ACTIVE_MCP_TOOL_SERVER_BY_NAME[item["function"]["name"]] = server_config
            except Exception:
                logger.exception(t("mcp_discover_failed") + f", server={server_config.get('name')}")
                console.print(Panel.fit(t("mcp_tool_unavailable", name=server_config.get("name") or str(server_config)), title=t("mcp_discover_failed")))

        ACTIVE_MCP_TOOL_DEFINITIONS = definitions
        ACTIVE_MCP_TOOL_CONFIGS = active_configs
        ACTIVE_MCP_TOOL_CONFIG = active_configs[0] if active_configs else {}
        logger.info("Discovered MCP tool definitions: %s", [item["function"]["name"] for item in definitions])
        return definitions
    except Exception:
        logger.exception(t("mcp_discover_failed"))
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []


def run_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_config = ACTIVE_MCP_TOOL_SERVER_BY_NAME.get(name) or ACTIVE_MCP_TOOL_CONFIGS[0] or ACTIVE_MCP_TOOL_CONFIG
    if not server_config:
        return {"status": "error", "content": t("mcp_tool_not_found", name=name)}

    async def _invoke() -> dict[str, Any]:
        async def _handler(session: ClientSession) -> dict[str, Any]:
            result = await session.call_tool(name, arguments or {})
            serialized = result.model_dump(mode="json")
            content = serialized.get("content")
            if isinstance(content, list):
                items = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if text is not None:
                            items.append(text)
                        else:
                            items.append(item)
                    else:
                        items.append(str(item))
                normalized = items[0] if len(items) == 1 else items
            else:
                normalized = content
            return {
                "status": "error" if bool(serialized.get("isError")) else "ok",
                "content": normalized,
            }

        return await _run_mcp_session(server_config, _handler)

    try:
        return asyncio.run(_invoke())
    except Exception:
        logger.exception(t("mcp_tool_failed") + f", tool={name}")
        return {"status": "error", "content": traceback.format_exc()}














def run_subprocess_command(command: Any, cwd: str, shell: bool = False, timeout: int | None = None) -> tuple[int, str, str]:
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    
    # 跨平台进程隔离配置
    popen_kwargs = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,  # 根治等待输入导致的卡死！
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True # 创建新进程组，便于后续杀整棵树

    try:
        proc = subprocess.Popen(command, shell=shell, **popen_kwargs)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning(t("tool_subprocess_failed"), cwd, safe_cwd, exc)
        proc = subprocess.Popen(command, cwd=str(Path.cwd()), shell=shell, **popen_kwargs)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 超时后杀掉整个进程树，防止孤儿进程
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=3)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        finally:
            try: proc.kill()
            except: pass
        return -1, "", f"Command timed out after {timeout} seconds"
    except KeyboardInterrupt:
        # 处理用户 Ctrl+C 中断
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        raise

    return proc.returncode, stdout or "", stderr or ""



import os
import signal
import subprocess
import time
import platform
from pathlib import Path




def execute_tool_call(tool_call: Any) -> dict[str, Any]:
    name = getattr(tool_call.function, "name", "")
    args = safe_parse_tool_args(getattr(tool_call.function, "arguments", {}))
    ensure_not_interrupted()
    logger.info(t("tool_execution", name=name, args=args))

    if name == "read_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="read_file", arg="path")}
            logger.error("read_file missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("read_file result: %s", result)
            return result

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            result = {
                "status": "error",
                "content": f"Unable to read as UTF-8 text: {path}\nThis file may be binary or encoded in another charset.",
            }
            logger.warning("read_file failed due to non-UTF8 content: %s", path)
            logger.info("read_file result: %s", result)
            return result
        except Exception as exc:
            result = {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
            logger.exception("read_file failed: %s", path)
            logger.info("read_file result: %s", result)
            return result

        result = {"status": "ok", "content": content}
        logger.info("read_file result: %s", result)
        return result

    if name == "write_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="write_file", arg="path")}
            logger.error("write_file missing path argument, raw args: %s", args)
            return result
        content = args.get("content", "")
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result = {"status": "ok", "content": f"Written: {path}"}
        logger.info("write_file result: %s", result)
        return result

    if name == "delete_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="delete_file", arg="path")}
            logger.error("delete_file missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("delete_file result: %s", result)
            return result
        path.unlink()
        result = {"status": "ok", "content": f"Deleted: {path}"}
        logger.info("delete_file result: %s", result)
        return result

    if name == "create_directory":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="create_directory", arg="path")}
            logger.error("create_directory missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path.mkdir(parents=True, exist_ok=True)
        result = {"status": "ok", "content": f"Created directory: {path}"}
        logger.info("create_directory result: %s", result)
        return result

    if name == "delete_directory":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="delete_directory", arg="path")}
            logger.error("delete_directory missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"Directory does not exist: {path}"}
            logger.info("delete_directory result: %s", result)
            return result
        shutil.rmtree(path)
        result = {"status": "ok", "content": f"Deleted directory: {path}"}
        logger.info("delete_directory result: %s", result)
        return result

    if name == "rename_path":
        old_path = args.get("old_path")
        new_path = args.get("new_path")
        if not old_path or not new_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="rename_path", arg="old_path/new_path")}
            logger.error("rename_path missing arguments, raw args: %s", args)
            return result
        old = Path(old_path).expanduser()
        new = Path(new_path).expanduser()
        if not old.is_absolute():
            old = Path.cwd() / old
        if not new.is_absolute():
            new = Path.cwd() / new
        if not old.exists():
            result = {"status": "error", "content": f"Path does not exist: {old}"}
            logger.info("rename_path result: %s", result)
            return result
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        result = {"status": "ok", "content": f"Renamed: {old} -> {new}"}
        logger.info("rename_path result: %s", result)
        return result

    if name == "copy_file":
        source_path = args.get("source_path")
        destination_path = args.get("destination_path")
        if not source_path or not destination_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="copy_file", arg="source_path/destination_path")}
            logger.error("copy_file missing arguments, raw args: %s", args)
            return result
        source = Path(source_path).expanduser()
        destination = Path(destination_path).expanduser()
        if not source.is_absolute():
            source = Path.cwd() / source
        if not destination.is_absolute():
            destination = Path.cwd() / destination
        if not source.exists():
            result = {"status": "error", "content": f"Source file does not exist: {source}"}
            logger.info("copy_file result: %s", result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        result = {"status": "ok", "content": f"Copied: {source} -> {destination}"}
        logger.info("copy_file result: %s", result)
        return result

    if name == "read_image_as_base64":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="read_image_as_base64", arg="path")}
            logger.error("read_image_as_base64 missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"Image file does not exist: {path}"}
            logger.info("read_image_as_base64 result: %s", result)
            return result

        result = {"status": "ok", "content": json.dumps({"path": str(path), "message": "Image loaded into context for multimodal analysis."}, ensure_ascii=False)}
        logger.info("read_image_as_base64 result: %s", result)
        return result

    if name == "list_dir":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": t("tool_missing_arg", name="list_dir", arg="path")}
            logger.error("list_dir missing path argument, raw args: %s", args)
            return result
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "ok", "content": json.dumps([], ensure_ascii=False)}
            logger.info("list_dir result: %s", result)
            return result

        recursive = bool(args.get("recursive", False))
        if recursive:
            items = []
            for item in sorted(path.rglob("*")):
                try:
                    items.append(str(item.resolve()))
                except OSError:
                    items.append(str(item))
        else:
            items = sorted([str(p) for p in path.iterdir()])
        result = {"status": "ok", "content": json.dumps(items, ensure_ascii=False)}
        logger.info("list_dir result: %s", result)
        return result

    if name == "search_code":
        file_path = args.get("path") or args.get("file_path")
        pattern = args.get("pattern")
        if not file_path or not pattern:
            result = {"status": "error", "content": t("tool_missing_arg", name="search_code", arg="path/pattern")}
            logger.error("search_code missing arguments, raw args: %s", args)
            return result

        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "ok", "content": json.dumps([], ensure_ascii=False)}
            logger.info("search_code result: %s", result)
            return result

        recursive = bool(args.get("recursive", True))
        max_results = args.get("max_results")
        try:
            max_results = int(max_results) if max_results is not None else 50
        except (TypeError, ValueError):
            max_results = 50

        matches: list[dict[str, Any]] = []
        if path.is_file():
            candidates = [path]
        elif recursive:
            candidates = [item for item in sorted(path.rglob("*")) if item.is_file()]
        else:
            candidates = [item for item in sorted(path.iterdir()) if item.is_file()]

        for candidate in candidates:
            try:
                content = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern in line:
                    matches.append({
                        "path": str(candidate.resolve()),
                        "line": line_number,
                        "content": line,
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        result = {"status": "ok", "content": json.dumps(matches, ensure_ascii=False)}
        logger.info("search_code result: %s", result)
        return result

    if name == "edit_file":
        file_path = args.get("path") or args.get("file_path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not file_path or old_string is None or new_string is None:
            result = {"status": "error", "content": t("tool_missing_arg", name="edit_file", arg="path/old_string/new_string")}
            logger.error("edit_file missing arguments, raw args: %s", args)
            return result

        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            result = {"status": "error", "content": f"File does not exist: {path}"}
            logger.info("edit_file result: %s", result)
            return result

        try:
            original_text = path.read_text(encoding="utf-8")
        except Exception as exc:
            result = {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
            logger.exception("edit_file failed: %s", path)
            logger.info("edit_file result: %s", result)
            return result

        if old_string not in original_text:
            result = {"status": "error", "content": f"Target text not found in file: {path}"}
            logger.info("edit_file result: %s", result)
            return result

        updated_text = original_text.replace(old_string, new_string, 1)
        path.write_text(updated_text, encoding="utf-8")
        result = {"status": "ok", "content": f"Updated: {path}"}
        logger.info("edit_file result: %s", result)
        return result

    if name == "execute_python_script":
        script = args.get("script")
        if not script:
            result = {"status": "error", "content": t("tool_missing_arg", name="execute_python_script", arg="script")}
            logger.error("execute_python_script missing script argument, raw args: %s", args)
            return result
        cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())
        script_path = Path(cwd) / "__cli_temp_script__.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        
        # 获取自定义超时，默认 120 秒，最大不超过 360 秒
        timeout_seconds = int(args.get("timeout_seconds") or 120)
        timeout_seconds = min(max(timeout_seconds, 10), TOOL_SUBPROCESS_TIMEOUT)
        
        returncode, stdout, stderr = run_subprocess_command(
            [sys.executable, str(script_path)], 
            cwd, 
            shell=False, 
            timeout=timeout_seconds
        )
        response = {
            "status": "ok" if returncode == 0 else "error",
            "content": stdout + stderr,
            "returncode": returncode,
        }
        logger.info("execute_python_script result: %s", response)
        return response


    if name in {item["function"]["name"] for item in ACTIVE_MCP_TOOL_DEFINITIONS}:
        logger.info("Executing MCP tool: %s, args: %s", name, args)
        return run_mcp_tool(name, args)

    result = {"status": "error", "content": t("unknown_tool", name=name)}
    logger.error("Unknown tool call: %s", name)
    return result


def sanitize_tool_result_for_display(result: dict[str, Any], max_len: int = 80) -> dict[str, Any]:
    """Return a shallow copy of result suitable for CLI display.

    If the 'content' field is a string longer than max_len, replace it with
    a placeholder that indicates it's omitted. For lists, replace long string
    items similarly. This avoids dumping huge text blobs to the terminal.
    """
    try:
        display = dict(result)
    except Exception:
        return {"status": "error", "content": "<unserializable result>"}

    content = display.get("content")
    if isinstance(content, str):
        if len(content) > max_len:
            display["content"] = f"<content omitted: length={len(content)} chars>"
    elif isinstance(content, list):
        new_list: list[Any] = []
        for item in content:
            if isinstance(item, str) and len(item) > max_len:
                new_list.append(f"<item omitted: length={len(item)} chars>")
            else:
                new_list.append(item)
        display["content"] = new_list
    return display


def show_stage(title: str, content: str) -> None:
    # If the content includes a JSON-formatted tool result (passed as "...\nresult={json}")
    # attempt to sanitize its 'content' field before printing to avoid very long output.
    if "\nresult=" in content:
        prefix, json_part = content.split("\nresult=", 1)
        try:
            parsed = json.loads(json_part)
            sanitized = sanitize_tool_result_for_display(parsed)
            content = f"{prefix}\nresult={json.dumps(sanitized, ensure_ascii=False)}"
        except Exception:
            # If it's not valid JSON, leave it as-is
            pass
    console.print(Panel.fit(content, title=title))


def show_tool_result(tool_call: Any, result: dict[str, Any]) -> None:
    display_result = sanitize_tool_result_for_display(result)
    description = get_tool_description(tool_call)
    description_text = f"{description}\n\n" if description else ""
    console.print(
        Panel.fit(
            f"[bold cyan]{tool_call.function.name}[/bold cyan]\n{description_text}{json.dumps(display_result, ensure_ascii=False, indent=2)}",
            title=t("tool_result_title"),
        )
    )


def chat_once(client: OpenAI, model: str, messages: list[dict[str, Any]], temperature: float, debug_enabled: bool = False, disable_tools:bool = False) -> Any:
    ensure_not_interrupted()
    wait_for_model_call_interval()
    tool_definitions = TOOL_DEFINITIONS + ACTIVE_MCP_TOOL_DEFINITIONS
    if disable_tools:
        tool_definitions = []
    request_payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "tools": tool_definitions,
        "tool_choice": "auto",
    }
    request_timeout = getattr(client, "timeout", None)
    if request_timeout is None:
        request_timeout = MODEL_REQUEST_TIMEOUT_SECONDS

    context_payload = json.dumps(request_payload, ensure_ascii=False)
    context_size_chars = len(context_payload)
    context_size_bytes = len(context_payload.encode("utf-8"))
    show_stage(t("start_model_call"), f"model={model}\nmessages={len(messages)}\n{t('context_size_label')}={context_size_chars} chars / {context_size_bytes} bytes")
    if debug_enabled:
        show_stage(t("model_request_params"), json.dumps(request_payload, ensure_ascii=False, indent=2))

    try:
        response = client.chat.completions.create(**request_payload, timeout=request_timeout)
        assistant_message = response.choices[0].message
        usage = getattr(response, "usage", None)
        if usage is not None:
            update_total_token_usage(usage)

        response_content = assistant_message.content or ""
        response_length = len(str(response_content))
        usage_summary = format_usage_summary(usage)
        show_stage(
            t("model_call_finished"),
            f"model={model}\nresponse_type={type(assistant_message).__name__}\n{t('response_length_label')}={response_length}\n{t('token_usage_label')}={usage_summary}",
        )
        if debug_enabled:
            raw_response = response.model_dump_json(indent=2)
            show_stage(t("model_raw_response"), raw_response)

        return assistant_message
    except KeyboardInterrupt:
        logger.info("Model call interrupted by user")
        show_stage(t("cancelled"), t("model_cancelled"))
        raise
    except Exception as exc:
        logger.exception(t("model_error"))
        err_text = str(exc)
        exc_name = type(exc).__name__
        status_code = getattr(exc, "status_code", None)

        if "429" in err_text or "RateLimit" in exc_name or t("quota_hint") in err_text or "RESOURCES_TIPS" in err_text:
            user_msg = t("model_quota_error")
        elif status_code == 502 or "502" in err_text or "InternalServerError" in exc_name or "Bad Gateway" in err_text:
            user_msg = t("model_502_error")
        else:
            user_msg = t("model_call_failed", error=f"{exc_name}: {err_text}")

        assistant_message = SimpleNamespace()
        assistant_message.tool_calls = []
        assistant_message.content = user_msg
        if debug_enabled:
            show_stage("Debug - model call error", traceback.format_exc())
        else:
            show_stage(t("model_call_failed", error=user_msg), user_msg)
        return assistant_message
    finally:
        mark_model_call_completed()


class ConversationRecorder:
    def __init__(self, md_path: Path, max_rounds: int = 50):
        self.md_path = md_path
        self.max_rounds = max_rounds
        # ensure file exists
        self.md_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.md_path.exists():
            self.md_path.write_text(t("recent_conversations_header") + "\n\n", encoding="utf-8")

    def _append(self, text: str) -> None:
        # Append text to the file
        with self.md_path.open("a", encoding="utf-8") as f:
            f.write(text)
        self._trim_rounds()

    def _trim_rounds(self) -> None:
        # Keep only the last self.max_rounds rounds (based on '## Round' headings)
        content = self.md_path.read_text(encoding="utf-8")
        parts = content.split("\n## Round ")
        if len(parts) <= self.max_rounds + 1:
            return
        # parts[0] is header before first round
        header = parts[0]
        rounds = parts[1:]
        keep = rounds[-self.max_rounds :]
        new_content = header + "\n## Round " + "\n## Round ".join(keep)
        self.md_path.write_text(new_content, encoding="utf-8")

    def start_round(self, user_text: str) -> None:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        header = f"\n## Round {ts}\n\n"
        user_block = f"**User:**\n\n{user_text}\n\n"
        self._append(header + user_block)

    def record_assistant(self, assistant_text: str) -> None:
        block = f"**Assistant:**\n\n{assistant_text}\n\n"
        self._append(block)

    def record_tool_start(self, tool_name: str, args: Any) -> None:
        try:
            args_text = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_text = str(args)
        block = f"**Tool Start:** {tool_name}\n\nArguments: {args_text}\n\n"
        self._append(block)

    def record_tool_result(self, tool_name: str, result: dict[str, Any]) -> None:
        status = result.get("status")
        content = result.get("content")
        try:
            content_text = json.dumps(content, ensure_ascii=False)
        except Exception:
            content_text = str(content)
        block = f"**Tool Result:** {tool_name} (status={status})\n\n{content_text}\n\n"
        self._append(block)

    def record_error(self, error_text: str) -> None:
        block = f"**Error:**\n\n{error_text}\n\n"
        self._append(block)


def to_tool_call_objects(tool_calls: list[Any]) -> list[Any]:
    converted: list[Any] = []
    for idx, tool_call in enumerate(tool_calls):
        if isinstance(tool_call, SimpleNamespace):
            converted.append(tool_call)
            continue

        function = getattr(tool_call, "function", None)
        if function is None:
            continue

        converted.append(
            SimpleNamespace(
                id=getattr(tool_call, "id", f"tool_call_{idx}"),
                type=getattr(tool_call, "type", "function"),
                function=SimpleNamespace(
                    name=getattr(function, "name", ""),
                    arguments=getattr(function, "arguments", {}),
                    description=getattr(function, "description", ""),
                ),
            )
        )
    return converted


def parse_tool_calls_from_content(content: str | None) -> list[Any]:
    if not content:
        return []

    text = content.strip()
    if not text:
        return []

    decoded_calls: list[Any] = []
    start = 0
    while True:
        start = text.find("{", start)
        if start == -1:
            break

        try:
            parsed, end = json.JSONDecoder().raw_decode(text[start:])
        except Exception:
            start += 1
            continue

        if isinstance(parsed, dict) and {"name", "arguments"}.issubset(parsed.keys()):
            name = str(parsed.get("name") or "").strip()
            arguments = parsed.get("arguments") or {}
            if name:
                decoded_calls.append(
                    SimpleNamespace(
                        id=f"content_tool_{len(decoded_calls)}",
                        type="function",
                        function=SimpleNamespace(name=name, arguments=arguments),
                    )
                )
            start += end
            continue

        start += 1

    return decoded_calls


def plan_user_request(client: OpenAI, model: str, history: list[dict[str, Any]], user_text: str, debug_enabled: bool = False) -> str:
    if is_special_command(user_text):
        return user_text

    planning_prompt = f"""
你是一个编程行业需求分析专家，你能够通过历史上下文和用户的输入分析出用户的真实意图，能够拆分步骤，并且能够生成任务执行清单。
在任务处理中，工具调用会产生大量中间信息，而对话只保存有限几轮上下文,在生成最终结果或者是某些步骤需要回溯前面缺失的历史信息的时候可以读取记忆文件:{MEMORY_FILE_PATH}。

### 请严格按照以下格式输出任务清单：

用户原始指令：......

结合上下文得到用户的完整意图：.....

接下来按照这个步骤逐步执行完成任务：
1、第一步：......
2、第二步：......
......

"""

    user_prompt = f"""请结合历史上下文分析用户输入并生成任务清单。
### 历史上下文:
{json.dumps(history, ensure_ascii=False)}

### 用户输入:
{user_text}
"""

    planning_messages = [
        {"role": "system", "content": planning_prompt},
        {"role": "user", "content": user_prompt},
    ]

    assistant_message = chat_once(client, model, planning_messages, temperature=0.2, debug_enabled=debug_enabled, disable_tools=True)
    return (assistant_message.content or "").strip() or user_text


def is_special_command(user_text: str) -> bool:
    normalized = (user_text or "").strip()
    if not normalized:
        return False
    special_commands = {"/exit", "/clear", "/task-start", "/task-end"}
    return normalized in special_commands


def run_agent(client: OpenAI, model: str, system_prompt: str, session_store: SessionStore, user_text: str, debug_enabled: bool = False, recorder: ConversationRecorder | None = None) -> None:
    reset_interruption_state()
    clear_task_memory_file()
    append_task_memory_entry(f"## Task started\n\nUser request: {summarize_memory_value(user_text, max_len=240)}\n\n")

    history = session_store.load()
    planned_user_text = user_text
    if not is_special_command(user_text):
        try:
            planned_user_text = plan_user_request(client, model, history, user_text, debug_enabled=debug_enabled)
        except Exception:
            logger.exception("Planning step failed; continuing with original user input")

    append_task_memory_entry("## Task checklist\n\n" + summarize_memory_value(planned_user_text, max_len=10000) + "\n\n")

    first_task_prompt = {"role": "user", "content": planned_user_text}
    history.append(first_task_prompt)
    session_store.save(history)
    if recorder:
        recorder.start_round(user_text)

    system_prompt_message = {"role": "system", "content": system_prompt}
    messages = []
    messages.extend(history)

    while True:
        try:
            finalize_prompt = []
            messages = messages[-CHAT_MESSAGE_MAX_COUNT:]  # Keep only the last N messages for context
            finalize_prompt.extend(messages)
            existing_first_prompt = first_task_prompt in messages
            if not existing_first_prompt:
                finalize_prompt = [first_task_prompt] + finalize_prompt
            finalize_prompt = [system_prompt_message] + finalize_prompt
            assistant_message = chat_once(client, model, finalize_prompt, temperature=0.2, debug_enabled=debug_enabled)
        except KeyboardInterrupt:
            logger.info("Current task interrupted by user")
            console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
            if recorder:
                recorder.record_error("User interrupted the current task")
            return
        except Exception:
            logger.exception("Chat request failed")
            console.print(Panel.fit(traceback.format_exc(), title=t("runtime_error_title")))
            if recorder:
                recorder.record_error(traceback.format_exc())
            return

        tool_calls = to_tool_call_objects(list(getattr(assistant_message, "tool_calls", []) or []))
        if not tool_calls:
            tool_calls = parse_tool_calls_from_content(getattr(assistant_message, "content", ""))

        if not tool_calls:
            answer = assistant_message.content or "I did not receive a valid reply."
            history.append({"role": "assistant", "content": answer})
            session_store.save(history)
            console.print(Panel.fit(answer, title="Agent reply"))
            if recorder:
                recorder.record_assistant(answer)
            return

        assistant_call_message = {
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_call_message)

        function_content = getattr(assistant_message,"content",None) or ""
        function_reasoning = getattr(assistant_message,"reasoning",None) or ""
        function_content_reasoning = getattr(assistant_message,"content_reasoning",None) or ""
        final_reasoning = function_reasoning or function_content_reasoning or function_content
        append_task_memory_entry(
            "### The reason for the assistant to call the function\n\n"
            f"{final_reasoning}\n\n"
        )

        show_stage(t("starting_tool_call"), f"reasoning:\n{final_reasoning}")

        for tc in tool_calls:
            try:
                result = execute_tool_call(tc)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
                show_tool_result(tc, result)
                append_task_memory_entry(
                    "### Tool invocation\n\n"
                    f"Tool: {tc.function.name}\n\n"
                    f"Arguments: {summarize_memory_value(tc.function.arguments, max_len=1200)}\n\n"
                    f"Result: {summarize_tool_result(result)}\n\n"
                )

                if tc.function.name == "read_image_as_base64":
                    try:
                        payload = json.loads(result.get("content", "{}")) if isinstance(result.get("content"), str) else result.get("content", {})
                        image_path = payload.get("path") if isinstance(payload, dict) else None
                        if image_path:
                            image_message = build_multimodal_user_message(
                                "The user attached an image for multimodal analysis. Please inspect it carefully.",
                                image_path,
                                max_bytes=120_000,
                            )
                            messages.append(image_message)
                    except Exception:
                        logger.exception("Failed to append multimodal image message")
            except KeyboardInterrupt:
                logger.info("Tool execution interrupted by user, tool=%s", tc.function.name)
                console.print(Panel.fit(t("current_tool_cancelled"), title=t("interrupted_title")))
                return
            except Exception:
                logger.exception("Tool execution error, tool=%s", tc.function.name)
                error_payload = {"status": "error", "content": traceback.format_exc()}
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(error_payload, ensure_ascii=False)})
                console.print(Panel.fit(traceback.format_exc(), title=t("tool_execution_error")))
                append_task_memory_entry(
                    "### Tool invocation\n\n"
                    f"Tool: {tc.function.name}\n\n"
                    f"Arguments: {summarize_memory_value(tc.function.arguments, max_len=1200)}\n\n"
                    f"Result: ERROR\n{summarize_tool_result(error_payload)}\n\n"
                )


def get_content_from_tool_calls(tool_calls: List) -> str:
    """
    Extract the `content` field from a list of tool call objects
    (as returned by OpenAI's ChatCompletionMessage.tool_calls).

    Args:
        tool_calls: List[ChatCompletionMessageFunctionToolCall] or similar.
                   Each item should have a `function.arguments` attribute
                   which is a JSON string containing a dict with a `content` key.

    Returns:
        The extracted `content` string if found, otherwise an empty string ("").
    """
    if not tool_calls:
        return ""

    for tool_call in tool_calls:
        try:
            # 兼容 dict 形式（比如有些 mock / 测试数据）
            if isinstance(tool_call, dict):
                func = tool_call.get("function")
                if not func:
                    continue
                # func 可能是 dict，也可能是有 arguments 属性的对象
                if isinstance(func, dict):
                    arguments_str = func.get("arguments")
                else:
                    arguments_str = getattr(func, "arguments", None)
            else:
                # OpenAI SDK 对象：ChatCompletionMessageFunctionToolCall
                func = getattr(tool_call, "function", None)
                if func is None:
                    continue
                # Function.arguments 是一个 JSON 字符串
                arguments_str = getattr(func, "arguments", None)

            if not arguments_str:
                continue

            # arguments 本身就是 JSON 字符串，需要 parse
            if isinstance(arguments_str, str):
                arguments = json.loads(arguments_str)
            elif isinstance(arguments_str, dict):
                arguments = arguments_str
            else:
                # 其他类型直接放弃
                continue

            # 真正取 content 字段
            if isinstance(arguments, dict) and "content" in arguments:
                return arguments["content"]

        except (json.JSONDecodeError, TypeError, AttributeError):
            # 解析失败或结构异常，忽略这条 tool_call
            continue

    return ""



def handle_task_end_command(md_path: Path, client: OpenAI, model: str, system_prompt: str, workdir: Path, debug_enabled: bool = False) -> None:
    """Process recent_conversations.md to find the most recent /task-start and use the
    subsequent rounds to ask the model to produce an improved prompt, then write last-prompt.md.
    """
    if not md_path.exists():
        console.print(Panel.fit(t("task_end_file_missing", path=md_path), title=t("task_end_error")))
        return

    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("\n## Round ")
    if len(parts) <= 1:
        console.print(Panel.fit(t("task_end_empty"), title=t("task_end_error")))
        return

    rounds = parts[1:]

    # Parse user text for each round (simple extraction)
    def extract_user_text(part: str) -> str:
        marker = "**User:**"
        idx = part.find(marker)
        if idx == -1:
            return ""
        start = idx + len(marker)
        # skip leading whitespace/newlines
        sub = part[start:]
        # end at next heading like '\n\n**' or end of part
        end_idx = sub.find("\n\n**")
        if end_idx == -1:
            end_idx = len(sub)
        return sub[:end_idx].strip()

    # Find most recent round index where user contains /task-start
    start_index = None
    for i in range(len(rounds) - 1, -1, -1):
        user_text = extract_user_text(rounds[i])
        if "/task-start" in user_text:
            start_index = i
            break

    if start_index is None:
        console.print(Panel.fit(t("task_end_no_task_start"), title=t("task_end_notice")))
        return

    selected = rounds[start_index:]
    compiled_text = "\n\n".join(["## Round " + r for r in selected])

    # Build messages for the model
    sys_prompt = f"""
你是提示词优化专家，负责将对话记录整理为更清晰、可执行的最终任务提示词。

### 生成最终提示词时，请严格遵循以下约束：
1. 只输出最终改进后的提示词文本，不要添加任何解释、元信息或注释。
2. 保留用户原始目标、关键上下文、澄清或变更后的要求、待执行步骤与预期产物。
3. 语言必须清晰、可执行、步骤化，适合直接给后续执行器使用。
4. 如果上下文中存在不确定信息，应显式写成“待确认”或“需验证”的约束项。
5. 输出长度控制在 18000 字符以内。

"""

    user_prompt = f"""分析对话记录帮我生成最终提示词,把最终提示词内容输出给我。
### 对话记录
{compiled_text}
"""

    try:
        assistant_message = chat_once(client, model, [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}], temperature=0.2, debug_enabled=debug_enabled, disable_tools=True)
    except Exception:
        logger.exception(t("task_end_generation_failed_log"))
        console.print(Panel.fit(traceback.format_exc(), title=t("task_end_generate_failed")))
        return
    prompt_call_tools = get_content_from_tool_calls(assistant_message.tool_calls)
    # console.print(f"{prompt_call_tools}")
    final_prompt = (assistant_message.content or "").strip()
    if not final_prompt:
        final_prompt = prompt_call_tools
    if not final_prompt:
        console.print(Panel.fit(t("task_end_no_prompt"), title=t("task_end_result")))
        return

    out_path = workdir / "last-prompt.md"
    out_text = t("final_prompt_header") + "\n\n" + final_prompt + "\n"
    out_path.write_text(out_text, encoding="utf-8")
    console.print(Panel.fit(t("task_end_completed", path=out_path), title=t("task_end_done")))


def interactive_loop(client: OpenAI, model: str, system_prompt: str, session_store: SessionStore, history_file: Path, debug_enabled: bool = False, recorder: ConversationRecorder | None = None) -> None:
    console.print(Panel.fit(
        "[bold green]Prompt Pilot CLI Coding Agent[/bold green]\n"
        + t("welcome_message") + "\n",
        title=t("startup_title"),
    ))

    slash_commands = ["/exit", "/clear", "/task-start", "/task-end"]
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=SlashCommandCompleter(slash_commands),
        complete_while_typing=True,
        bottom_toolbar=build_bottom_toolbar_text,
    )
    while True:
        try:
            user_text = session.prompt(t("prompt_placeholder"))
        except KeyboardInterrupt:
            console.print("\n" + t("exit_message"))
            return

        if user_text.strip() == "/exit":
            console.print(t("bye_message"))
            return
        if user_text.strip() == "/clear":
            session_store.save([])
            recent_conversations_path = ROOT / "recent_conversations.md"
            if recent_conversations_path.exists():
                recent_conversations_path.write_text(t("recent_conversations_header") + "\n\n", encoding="utf-8")
            console.print(t("clear_session_message"))
            continue
        if user_text.strip() == "/task-end":
            # Process recent_conversations.md and generate last-prompt.md
            md_path = recorder.md_path if recorder else (ROOT / "recent_conversations.md")
            handle_task_end_command(md_path, client, model, system_prompt, WORKSPACE_DIR, debug_enabled=debug_enabled)
            continue

        run_agent(client, model, system_prompt, session_store, user_text, debug_enabled=debug_enabled, recorder=recorder)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=t("cli_parser_description"))
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {APPLICATION_VERSION}")
    parser.add_argument("-t", "--task", help=t("task_argument_help"))
    parser.add_argument("-d", "--workdir", default=WORKSPACE_DIR, help=t("workdir_argument_help"))
    parser.add_argument("-l", "--lang", default="en", help="language code for localization (default: en)")
    parser.add_argument("-amc", "--agent-messages-count", default=CHAT_MESSAGE_MAX_COUNT, help="number of messages to keep in agent history (default: 6)")
    parser.add_argument("-rd", "--request-delay", default=RE_ACTION_DELAY, help="delay in seconds between model requests (default: 8)")
    parser.add_argument("-hc", "--history-count", default=DEFAULT_MAX_CHAT_COUNT, help="number of rounds to keep in conversation history (default: 5)")
    parser.add_argument("--reset-session", action="store_true", help=t("reset_session_help"))
    return parser


def main() -> None:
    global UI_SYSTEM_LANGUAGE, DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, DEFAULT_MAX_CHAT_COUNT, CHAT_MESSAGE_MAX_COUNT

    parser = build_cli_parser()
    args = parser.parse_args()

    RE_ACTION_DELAY = int(args.request_delay)
    DEFAULT_MAX_CHAT_COUNT = int(args.history_count)
    CHAT_MESSAGE_MAX_COUNT = int(args.agent_messages_count)

    # Set localization language
    UI_SYSTEM_LANGUAGE = args.lang

    # set up workspace directory
    WORKSPACE_DIR = Path(args.workdir)
    console.print(Panel.fit(t("workdir_message", path=WORKSPACE_DIR), title=t("cli_config_title")))

    try:
        model_cfg, system_prompt = ensure_config(workdir=WORKSPACE_DIR)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WORKSPACE_DIR)

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)

    mcp_tools = discover_mcp_tools(model_cfg)
    if mcp_tools:
        console.print(Panel.fit(t("mcp_connected", count=len(mcp_tools)), title=t("mcp_config_title")))

    try:
        client = build_client(model_cfg)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    session_file = ROOT / ".session_history.json"
    history_file = ROOT / ".history"

    session_store = SessionStore(session_file, max_messages=DEFAULT_MAX_CHAT_COUNT)
    if args.reset_session:
        session_store.save([])
        recent_conversations_path = ROOT / "recent_conversations.md"
        if recent_conversations_path.exists():
            recent_conversations_path.write_text(t("recent_conversations_header") + "\n\n", encoding="utf-8")
        console.print(t("session_reset_message"))

    debug_enabled = bool(model_cfg.get("debug", False))
    if debug_enabled:
        console.print(Panel.fit(t("debug_enabled_message"), title=t("debug_config_title")))

    # Create conversation recorder to persist recent rounds to markdown
    recorder = ConversationRecorder(ROOT / "recent_conversations.md", max_rounds=188)

    if args.task:
        try:
            run_agent(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, args.task, debug_enabled=debug_enabled, recorder=recorder)
        except KeyboardInterrupt:
            console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
        return

    try:
        interactive_loop(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, history_file, debug_enabled=debug_enabled, recorder=recorder)
    except KeyboardInterrupt:
        console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        console.print(Panel.fit(traceback.format_exc(), title=t("runtime_error_title")))
        sys.exit(1)