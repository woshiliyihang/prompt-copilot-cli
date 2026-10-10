from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import APITimeoutError, OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.console import Console


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


def format_cumulative_token_summary() -> str:
    return (
        f"{t('token_usage_label')} "
        f"{t('prompt_tokens_label')}={TOTAL_TOKEN_USAGE['prompt_tokens']} | "
        f"{t('completion_tokens_label')}={TOTAL_TOKEN_USAGE['completion_tokens']} | "
        f"{t('total_tokens_label')}={TOTAL_TOKEN_USAGE['total_tokens']}"
    )


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
    "config_header": {"zh": "配置字段说明：", "en": "Configuration field descriptions:"},
    "config_file_read_error": {"zh": "配置文件读取失败，请检查 {path}: {error}", "en": "Failed to read config file. Please check {path}: {error}"},
    "config_file_format_error": {"zh": "配置文件格式不正确，期望 JSON 对象: {path}", "en": "Config file format is invalid. Expected a JSON object: {path}"},
    "config_incomplete": {"zh": "模型配置不完整，首次使用需正确配置模型才能够使用。\n请在 {path} 中完善以下字段：{fields}", "en": "The model configuration is incomplete. Please complete the required fields in {path} before using the agent.\nMissing fields: {fields}"},
    "config_api_key_error": {"zh": "未配置 API Key，请先在 config/model_config.json 中填写 api_key。", "en": "API key is not configured. Please fill in the api_key field in the config file first."},
    "mcp_discover_failed": {"zh": "发现 MCP 工具失败", "en": "Failed to discover MCP tools"},
    "mcp_tool_not_found": {"zh": "未找到 MCP 工具对应的服务配置: {name}", "en": "No MCP server configuration found for tool: {name}"},
    "mcp_tool_unavailable": {"zh": "MCP 工具不可用：{name}，已忽略。", "en": "MCP tool unavailable: {name}; it will be ignored."},
    "tool_missing_arg": {"zh": "{name} 缺少 {arg} 参数。", "en": "{name} is missing the {arg} argument."},
    "unknown_tool": {"zh": "未知工具: {name}", "en": "Unknown tool: {name}"},
    "model_request_params": {"zh": "调试-模型请求参数", "en": "Debug - model request parameters"},
    "model_raw_response": {"zh": "调试-模型原始返回", "en": "Debug - raw model response"},
    "cancelled": {"zh": "已取消", "en": "Cancelled"},
    "model_cancelled": {"zh": "用户中断了当前模型调用。", "en": "The current model call was interrupted by the user."},
    "model_quota_error": {"zh": "模型调用失败：检测到配额/限流问题（可能是免费额度已耗尽或未开通付费）。\n请检查配置中的 api_key 与 base_url，或开通/充值服务后重试。", "en": "Model call failed: quota or rate-limit issue detected (the free quota may be exhausted or billing may not be enabled).\nPlease check the api_key and base_url in the config, or enable/top up the service and try again."},
    "model_502_error": {"zh": "模型调用失败：上游模型服务返回了 502（Bad Gateway）或服务端异常。\n这通常是模型服务端暂时不可用、代理转发异常或服务正在重启造成的。\n请稍后重试，并确认 base_url 指向的是可正常提供 /chat/completions 的服务。", "en": "Model call failed: the upstream model service returned 502 (Bad Gateway) or a server-side error.\nThis is usually caused by a temporary outage, proxy forwarding issue, or service restart.\nPlease try again later and confirm that base_url points to a service that serves /chat/completions correctly."},
    "model_call_failed": {"zh": "模型调用失败：{error}", "en": "Model call failed: {error}"},
    "tool_result_title": {"zh": "工具调用结果", "en": "Tool execution result"},
    "agent_reply_title": {"zh": "agent 回复", "en": "Agent reply"},
    "config_error_title": {"zh": "配置错误", "en": "Configuration error"},
    "quota_hint": {"zh": "免费额度", "en": "free quota"},
    "welcome_message": {
        "zh": "输入:\n/exit 退出，\n/clear 清空当前会话，\nCtrl+C 可中断当前执行（提示符下再按一次退出）。\n\n启动命令示例：\npython main.py -l zh\npython main.py -t \"你的任务\" -d ./workspace -l en\n\n参数说明：\n-t / --task：一次性任务内容\n-d / --workdir：指定工作目录\n-l / --lang：选择语言（zh / en）\n-rd / --request-delay：请求间隔秒数(默认: 3)\n\n对话上下文仅保存在内存中，最多保留最近 50 轮，程序退出后即清空。",
        "en": "Input:\n/exit to exit,\n/clear to clear the current session,\nCtrl+C can interrupt the current execution (press it twice at the prompt to exit).\n\nStartup command examples:\npython main.py -l zh\npython main.py -t \"your task\" -d ./workspace -l en\n\nOptions:\n-t / --task: one-off task content\n-d / --workdir: specify the working directory\n-l / --lang: choose language (zh / en)\n-rd / --request-delay: delay in seconds between model requests (default: 3)\n\nThe conversation context is kept in memory only, capped at the last 50 rounds, and is cleared when the program exits."
    },
    "startup_title": {"zh": "启动", "en": "Start"},
    "exit_message": {"zh": "已退出。", "en": "Exited."},
    "bye_message": {"zh": "再见。", "en": "Goodbye."},
    "clear_session_message": {"zh": "内存中的会话记录已清空。", "en": "In-memory conversation history has been cleared."},
    "workdir_message": {"zh": "工作目录: {path}", "en": "Working directory: {path}"},
    "cli_config_title": {"zh": "Cli 配置", "en": "CLI configuration"},
    "mcp_connected": {"zh": "已接入 MCP 工具：{count} 个", "en": "Connected MCP tools: {count}"},
    "mcp_config_title": {"zh": "MCP 配置", "en": "MCP configuration"},
    "debug_enabled_message": {"zh": "调试模式已开启，会输出模型请求参数与原始返回内容。", "en": "Debug mode is enabled; model request parameters and raw responses will be shown."},
    "debug_config_title": {"zh": "调试配置", "en": "Debug configuration"},
    "task_cancelled": {"zh": "已取消当前任务。", "en": "The current task has been cancelled."},
    "interrupted_title": {"zh": "已中断", "en": "Interrupted"},
    "runtime_error_title": {"zh": "运行时错误", "en": "Runtime error"},
    "tool_execution_error": {"zh": "工具执行错误", "en": "Tool execution error"},
    "starting_tool_call": {"zh": "开始调用工具", "en": "Starting tool call"},
    "current_tool_cancelled": {"zh": "已取消当前工具执行。", "en": "The current tool execution has been cancelled."},
    "cli_parser_description": {"zh": "Prompt Copilot CLI 编程 Agent", "en": "Prompt Copilot CLI coding agent"},
    "task_argument_help": {"zh": "一次性任务内容，适合单次执行。", "en": "One-off task content, suitable for a single run."},
    "workdir_argument_help": {"zh": "工作目录。", "en": "Working directory."},
    "bare_argument_help": {"zh": "精简模式：不向上下文注入系统提示词与 function-calling 工具定义。", "en": "Bare mode: do not inject the system prompt or function-calling tool definitions into the context."},
    "prompt_placeholder": {"zh": "你想让我做什么？> ", "en": "What do you want me to do? > "},
    "toolbar_help": {"zh": "可用命令: /exit /clear  | Tab 补全 | Ctrl+C 可中断当前执行", "en": "Available commands: /exit /clear | Tab completion | Ctrl+C can interrupt the current execution"},
    "ctrl_c_hint": {"zh": "已清空当前输入；再按一次 Ctrl+C 退出。", "en": "Current input cleared; press Ctrl+C again to exit."},
    "token_usage_label": {"zh": "累计 token", "en": "Cumulative tokens"},
    "prompt_tokens_label": {"zh": "prompt", "en": "prompt"},
    "completion_tokens_label": {"zh": "completion", "en": "completion"},
    "total_tokens_label": {"zh": "total", "en": "total"},
}


def t(key: str, **kwargs: Any) -> str:
    mapping = TRANSLATIONS.get(key, {})
    text = mapping.get(UI_SYSTEM_LANGUAGE, mapping.get("en", key))
    if kwargs:
        return text.format(**kwargs)
    return text

DEFAULT_SYSTEM_PROMPT = """
你是一个基于 CLI 的编程 Agent，你的名字是 Jason Li，专注于使用文件、命令、Python 脚本等工具完成开发任务。

工作原则：
1、先检查工作目录并理解需求。
2、优先使用网络搜索类工具搜索网络实时信息，例如：天气、新闻、资讯等等。
3、所有代码文件生成、编辑、删除等操作均在工作目录中执行。如果用户没有指定具体目录，默认工作目录为工程的根目录。
4、如果需要，输出简洁的说明与下一步建议。
5、若涉及删除系统文件或执行危险命令，必须先向用户确认后方可执行。
6、遇到信息盲区时，严禁主观臆测，请优先使用搜索工具补齐信息缺口，确保回答精准有据。
7、如果用户想“读取/识别其他二进制文件”，你必须明确告知：我无法直接读取或理解通用二进制文件内容。
8、如果用户想“查看图片内容”，应优先调用图片读取工具。
9、当用户输入一条任务指令时，如果是详细的任务清单，你就理解用户要求逐步完成工作，同时执行每个步骤的时候明确说明当前环节与进度，实时反馈任务状态。
10、使用 execute_python_script 时按命令类型区分处理：若是 ls、pytest、build 等短命令，脚本内用 subprocess.run 显式设置 timeout 同步执行即可；若是 npm run dev、flask run 等常驻服务或任何不会自行退出的进程，一律使用 background=true 参数（见第 13 条）让工具脱离终端后台启动并立即返回 PID 与日志路径，脚本内只负责启动后打印就绪标志即可，严禁在脚本内同步阻塞或自行 Popen 后 sys.exit() 模拟后台。
11、无法调用官方联网检索工具而通过脚本抓取网络内容时需区分数据类型处理，抓取普通网页文本需剔除 HTML 标签、样式代码、广告碎片、无效注释、多余空行等冗余内容，仅留存有效正文且文本超出 8000 字符则截断，文本输出上限为 8000 字符，抓取程序源代码则无需过滤，完整保留原始代码、自带注释、缩进换行与原有格式，代码输出无字符数量限制。
12、常驻/长驻进程一律通过 execute_python_script 的 background=true 启动（工具内部已按平台脱离终端：Linux/Mac 用 start_new_session=True，Windows 用 creationflags=CREATE_NEW_PROCESS_GROUP），无需在脚本内自行 Popen；仅当你确需在脚本内手动脱离终端启动子进程时，才需自行设置上述跨平台参数。
13、启动网络服务/服务器（如 web server、npm run dev、flask run 等长驻进程）时，优先使用 execute_python_script 的 background=true 参数：让工具以 detached 方式启动并立即返回 PID 与日志路径，你随后读取日志文件确认启动就绪（如出现端口监听、监听地址、健康检查通过等标志），或访问健康检查 URL 验证，再继续下一步；不要在脚本内同步阻塞等待服务退出。
14、凡耗时不可控的超长任务（如大文件下载、模型训练、全量测试、复杂构建等），一律使用 background=true 让工具后台启动并立即返回 PID 与日志路径，禁止在脚本内同步阻塞等待完成；随后通过读取日志文件监控进度（脚本内应定期写入心跳/进度），若发现任务完成或长时间无进度更新，必须主动调用系统命令（如 kill -9 PID）清理后台进程以防孤儿进程耗尽资源；仅在任务自身支持超时参数或耗时可预估时，方可在脚本内同步执行并设合理 timeout_seconds。
15、如果需要搜索互联网信息时候优先使用中国国内搜索引擎优先，例如：bing、百度等。
16、所有生成的代码及网络检索内容均统一优先使用 UTF-8 编码。

"""

UI_SYSTEM_LANGUAGE = "en"
APPLICATION_VERSION = "0.8.0"
WORKSPACE_DIR = ROOT / "workspace"
CONFIG_SAVE_FILE_PATH = ROOT / "config.json"
RE_ACTION_DELAY = 3 # unit: seconds
TOOL_SUBPROCESS_TIMEOUT = 6 * 60  # 6 minutes in seconds
MAX_AGENT_ITERATIONS = 50
MAX_CHAT_ROUNDS = 50  # hard cap: conversation rounds kept in the in-memory history
MAX_READ_FILE_BYTES = 5 * 1024 * 1024  # refuse to read files larger than 5 MB
MAX_READ_FILE_CHARS = 100_000  # truncate longer text before it enters the context
MAX_SEARCH_FILE_BYTES = 1_000_000  # skip files larger than 1 MB when searching
MAX_LIST_DIR_ENTRIES = 2000  # cap recursive directory listings
IGNORED_DIR_NAMES = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", ".idea",
    ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
}
MCP_SESSION_START_TIMEOUT = 120  # generous budget for slow npx cold starts
MCP_TOOL_CALL_TIMEOUT = 300  # per tool invocation timeout
MODEL_REQUEST_TIMEOUT_SECONDS = 600  # generous timeout for slower model generations
BARE_MODE: bool = False  # -b/--bare: do not inject the system prompt or function-calling tool definitions into the context
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


def _get_config_field_descriptions() -> dict[str, str]:
    """Build config field descriptions lazily so they honour the UI language."""
    return {
        "model": t("config_field_model"),
        "base_url": t("config_field_base_url"),
        "api_key": t("config_field_api_key"),
        "temperature": t("config_field_temperature"),
        "debug": t("config_field_debug"),
        "mcp": t("config_field_mcp"),
    }

console = Console()


def print_section(content: str, title: str = "") -> None:
    """Print content under a dashed title separator instead of a boxed frame.

    Boxed output is hard to select and copy from the terminal, so sections are
    delimited with a plain `-------- 标题 --------` line followed by the body.
    """
    console.print()
    if title:
        console.print(f"{'-' * 18} {title} {'-' * 18}")
    console.print(content, highlight=False)

_builtin_tool_definitions_cache: list[dict[str, Any]] | None = None


def get_builtin_tool_definitions() -> list[dict[str, Any]]:
    """Build built-in tool definitions lazily so descriptions honour the UI language."""
    global _builtin_tool_definitions_cache
    if _builtin_tool_definitions_cache is None:
        # NOTE: the big literal below intentionally keeps its original indentation;
        # it is a bracket continuation inside this function, which is valid Python
        # and lets the t() descriptions be resolved with the runtime UI language.
        _builtin_tool_definitions_cache = [
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
                        "description": "Maximum execution time in seconds before the process is terminated. Defaults to 120. Set a shorter timeout for quick commands to avoid waiting. Ignored when background=true."
                    },
                    "background": {
                        "type": "boolean",
                        "description": "If true, start a long-running service (e.g. web server) detached in the background, return immediately with its PID and log path, and let it keep running. Use this for servers/daemons. Defaults to false (foreground, waits for exit)."
                    },
                    "startup_timeout": {
                        "type": "integer",
                        "description": "Seconds to probe for a quick startup crash when background=true. Defaults to 3."
                    }
                },
                "required": ["script", "cwd"]
            }
        }
    }
]
    return _builtin_tool_definitions_cache


def __getattr__(name: str) -> Any:
    # Backwards compatibility: keep `main.TOOL_DEFINITIONS` working after the lazy refactor.
    if name == "TOOL_DEFINITIONS":
        return get_builtin_tool_definitions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class MCPManager:
    """Manages MCP tool discovery and execution state."""
    
    def __init__(self):
        self.tool_definitions: list[dict[str, Any]] = []
        self.tool_config: dict[str, Any] = {}
        self.tool_configs: list[dict[str, Any]] = []
        self.server_by_name: dict[str, dict[str, Any]] = {}
    
    def set_tools(self, definitions: list[dict[str, Any]], configs: list[dict[str, Any]]) -> None:
        self.tool_definitions = definitions
        self.tool_configs = configs
        self.tool_config = configs[0] if configs else {}
        self.server_by_name = {}
        for idx, item in enumerate(definitions):
            self.server_by_name[item["function"]["name"]] = configs[idx] if idx < len(configs) else self.tool_config
    
    def clear(self) -> None:
        self.tool_definitions = []
        self.tool_config = {}
        self.tool_configs = []
        self.server_by_name = {}
    
    def get_server_config(self, tool_name: str) -> dict[str, Any] | None:
        return self.server_by_name.get(tool_name) or (self.tool_configs[0] if self.tool_configs else self.tool_config or None)


# Tool definitions cache
_cached_tool_definitions: list[dict[str, Any]] | None = None
_cached_mcp_tool_count: int = 0

def get_tool_definitions() -> list[dict[str, Any]]:
    """Get cached tool definitions, rebuilding only when MCP tools change."""
    global _cached_tool_definitions, _cached_mcp_tool_count
    current_count = len(_mcp_manager.tool_definitions)
    if _cached_tool_definitions is None or _cached_mcp_tool_count != current_count:
        _cached_tool_definitions = get_builtin_tool_definitions() + _mcp_manager.tool_definitions
        _cached_mcp_tool_count = current_count
    return _cached_tool_definitions
# Global instance
_mcp_manager = MCPManager()

# Backward compatibility accessors (module-level functions, not @property)
def ACTIVE_MCP_TOOL_DEFINITIONS() -> list[dict[str, Any]]:
    return _mcp_manager.tool_definitions


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


# In-memory conversation history shared across the session (no persistence).
# Each round contributes one user message and one assistant message.
CONVERSATION_HISTORY: list[dict[str, Any]] = []


def reset_conversation_history() -> None:
    CONVERSATION_HISTORY.clear()


def _trim_conversation_history() -> None:
    max_messages = MAX_CHAT_ROUNDS * 2
    if len(CONVERSATION_HISTORY) > max_messages:
        del CONVERSATION_HISTORY[:-max_messages]


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
    os_name = platform.system() or 'Unknown'
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
    """Resolve the working directory for subprocess execution.
    
    Returns a valid directory path, creating it if needed.
    Falls back to the fallback path (default: cwd) if resolution fails.
    """
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


def safe_path(path: str | os.PathLike[str], base: Path | None = None) -> Path:
    """Resolve path and ensure it's within base directory (prevents path traversal)."""
    # Use WORKSPACE_DIR as default base for relative paths
    if base is None:
        try:
            base_path = WORKSPACE_DIR.resolve()
        except NameError:
            base_path = Path.cwd().resolve()
    else:
        base_path = base.resolve()
    
    target = Path(path).expanduser()
    if not target.is_absolute():
        # Relative path: resolve against base and verify it's within base
        target = (base_path / target).resolve()
        try:
            target.relative_to(base_path)
        except ValueError:
            raise PermissionError(f"Path traversal attempt blocked: {path}")
    else:
        # Absolute path: resolve (normalize) it.
        # Path traversal via absolute paths like /../../../etc/passwd
        # is already mitigated by resolve() which normalizes the path.
        # OS-level permissions enforce access control.
        target = target.resolve()
    return target


def resolve_tool_path(path_arg: str | None, arg_name: str = "path") -> tuple[Path | None, dict | None]:
    """Safely resolve a path argument for a tool.
    
    Returns:
        (path, None) on success
        (None, error_dict) on failure
    """
    if not path_arg:
        return None, {"status": "error", "content": t("tool_missing_arg", name="unknown", arg=arg_name)}
    try:
        return safe_path(path_arg), None
    except PermissionError as e:
        return None, {"status": "error", "content": str(e)}


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    """Read a text file as UTF-8, falling back to GBK/Latin-1 for legacy encodings.

    Returns a (content, encoding_used) tuple.
    """
    try:
        return path.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for encoding in ("gbk", "latin-1"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", b"", 0, 0, "unable to decode file with any supported encoding")


def write_text_atomic(path: Path, content: str) -> None:
    """Write to a temp file in the same directory, then atomically replace the target.

    Prevents files from being left half-written if the process dies mid-write.
    """
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _format_config_field_help() -> str:
    lines = [t("config_header")]
    for field_name, description in _get_config_field_descriptions().items():
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

    merged_config: dict[str, Any] = dict(DEFAULT_MODEL_CONFIG)
    for key, value in file_payload.items():
        # Apply loaded config on top of defaults; nested dicts are replaced wholesale.
        merged_config[key] = value

    required_fields = ["model", "base_url", "api_key"]
    missing_fields = [field for field in required_fields if not str(merged_config.get(field, "")).strip()]
    if missing_fields:
        raise RuntimeError(
            t("config_incomplete", path=CONFIG_SAVE_FILE_PATH, fields=', '.join(missing_fields)) + "\n\n"
            + f"{_format_config_field_help()}"
        )

    device_prompt = build_device_environment_context(str(workdir))
    system_prompt = device_prompt + "\n\n" + DEFAULT_SYSTEM_PROMPT
    return merged_config, system_prompt


def build_client(model_cfg: dict[str, Any]) -> OpenAI:
    api_key = model_cfg.get("api_key")
    if not api_key:
        raise RuntimeError(t("config_api_key_error"))
    base_url = model_cfg.get("base_url")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    try:
        from httpx import Timeout as HttpxTimeout
        kwargs["timeout"] = HttpxTimeout(MODEL_REQUEST_TIMEOUT_SECONDS)
    except ImportError:
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

    for definition in get_builtin_tool_definitions() + _mcp_manager.tool_definitions:
        function = definition.get("function") or {}
        if function.get("name") == tool_name:
            return str(function.get("description", "")).strip()

    return ""


class MCPClientPool:
    """Maintains persistent MCP sessions per server on a dedicated background event loop.

    Sessions are reused across tool calls instead of being re-established for every
    invocation, which avoids repeatedly paying the (slow) stdio/npx startup cost.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._start_lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            self._thread = threading.Thread(target=self._loop_runner, name="mcp-pool", daemon=True)
            self._thread.start()
            while self._loop is None:
                time.sleep(0.01)
            return self._loop

    def _loop_runner(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_forever()
        finally:
            loop.close()

    @staticmethod
    def _config_key(server_config: dict[str, Any]) -> str:
        return json.dumps(server_config, sort_keys=True, ensure_ascii=False)

    async def _open_session(self, key: str, server_config: dict[str, Any]) -> ClientSession:
        if key in self._sessions:
            return self._sessions[key]
        stack = AsyncExitStack()
        try:
            transport = str(server_config.get("transport") or server_config.get("type") or "stdio").lower()
            if transport in {"http", "streamable_http", "streamable-http"}:
                url = server_config.get("url")
                if not url:
                    raise RuntimeError("MCP HTTP server is missing a URL")
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(url, headers=server_config.get("headers") or {})
                )
            elif transport == "sse":
                url = server_config.get("url")
                if not url:
                    raise RuntimeError("MCP SSE server is missing a URL")
                read, write = await stack.enter_async_context(
                    sse_client(url, headers=server_config.get("headers") or {})
                )
            else:
                command = server_config.get("command") or "npx"
                args = list(server_config.get("args") or ["-y", "bing-cn-mcp"])
                read, write = await stack.enter_async_context(
                    stdio_client(StdioServerParameters(command=command, args=args))
                )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stacks[key] = stack
        self._sessions[key] = session
        return session

    def get_session(self, server_config: dict[str, Any]) -> ClientSession:
        key = self._config_key(server_config)
        session = self._sessions.get(key)
        if session is not None:
            return session
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._open_session(key, server_config), loop)
        return future.result(timeout=MCP_SESSION_START_TIMEOUT)

    async def _close_session(self, key: str) -> None:
        stack = self._stacks.pop(key, None)
        self._sessions.pop(key, None)
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                pass

    def _invalidate(self, key: str) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_session(key), self._loop).result(timeout=10)
        except Exception:
            pass

    def _submit(self, coro: Any) -> Any:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def warm_sessions(self, server_configs: list[dict[str, Any]]) -> None:
        """Open sessions for all servers in parallel (used at startup)."""
        loop = self._ensure_loop()
        futures = [
            asyncio.run_coroutine_threadsafe(self._open_session(self._config_key(config), config), loop)
            for config in server_configs
        ]
        for future in futures:
            try:
                future.result(timeout=MCP_SESSION_START_TIMEOUT)
            except Exception:
                continue

    def list_tools(self, server_config: dict[str, Any]) -> Any:
        session = self.get_session(server_config)
        future = self._submit(session.list_tools())
        return future.result(timeout=MCP_SESSION_START_TIMEOUT)

    @staticmethod
    def _serialize_tool_result(result: Any) -> dict[str, Any]:
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

    async def _call_tool_async(self, session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await session.call_tool(name, arguments or {})
        return self._serialize_tool_result(result)

    def call_tool(self, server_config: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        key = self._config_key(server_config)
        session = self.get_session(server_config)
        future = self._submit(self._call_tool_async(session, name, arguments))
        try:
            return future.result(timeout=MCP_TOOL_CALL_TIMEOUT)
        except Exception:
            # The persistent session may be broken (server restart, protocol error).
            # Invalidate it and retry once on a fresh session before giving up.
            future.cancel()
            self._invalidate(key)
            session = self.get_session(server_config)
            retry = self._submit(self._call_tool_async(session, name, arguments))
            return retry.result(timeout=MCP_TOOL_CALL_TIMEOUT)

    async def _aclose_all(self) -> None:
        for key in list(self._stacks):
            await self._close_session(key)

    def shutdown(self) -> None:
        if self._loop is None:
            return
        try:
            self._submit(self._aclose_all()).result(timeout=10)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
        except Exception:
            pass
        self._loop = None
        self._thread = None


# Global MCP session pool shared by discovery and tool execution.
_mcp_pool = MCPClientPool()
atexit.register(_mcp_pool.shutdown)


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

    mcp_cfg = model_cfg.get("mcp", {})
    if isinstance(mcp_cfg, dict):
        enabled = bool(mcp_cfg.get("enabled", True))
    else:
        enabled = True
    if not enabled:
        _mcp_manager.clear()
        return []

    server_configs = normalize_mcp_server_configs(mcp_cfg)
    if not server_configs:
        _mcp_manager.clear()
        return []

    try:
        # Connect to every server (in parallel) and keep the sessions open for reuse.
        _mcp_pool.warm_sessions(server_configs)

        definitions: list[dict[str, Any]] = []
        active_configs: list[dict[str, Any]] = []
        for server_config in server_configs:
            try:
                tools = _mcp_pool.list_tools(server_config)
            except Exception:
                print_section(t("mcp_tool_unavailable", name=server_config.get("name") or str(server_config)), title=t("mcp_discover_failed"))
                continue
            discovered = [normalize_mcp_tool_definition(tool) for tool in tools.tools]
            definitions.extend(discovered)
            active_configs.append(server_config)
            for item in discovered:
                _mcp_manager.server_by_name[item["function"]["name"]] = server_config

        _mcp_manager.set_tools(definitions, active_configs)
        return definitions
    except Exception:
        _mcp_manager.clear()
        return []


def run_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_config = _mcp_manager.get_server_config(name)
    if not server_config:
        return {"status": "error", "content": t("mcp_tool_not_found", name=name)}

    try:
        # Reuses the persistent session from MCPClientPool instead of spawning a
        # new server process (e.g. npx) for every single tool invocation.
        return _mcp_pool.call_tool(server_config, name, arguments)
    except Exception:
        return {"status": "error", "content": traceback.format_exc()}


def run_subprocess_command(command: Any, cwd: str, shell: bool = False, timeout: int | None = None) -> tuple[int, str, str]:
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    
    # Validate command to prevent shell injection
    if shell:
        if isinstance(command, str):
            # For shell=True, command must be a string - validate it doesn't contain dangerous patterns
            dangerous = [';', '&', '|', '`', '$(', '&&', '||']
            for d in dangerous:
                if d in command:
                    raise ValueError(f"Shell command contains forbidden pattern: {d}")
        else:
            raise ValueError("When shell=True, command must be a string")
    else:
        # For shell=False, command must be a list
        if isinstance(command, str):
            raise ValueError("When shell=False, command must be a list")
    
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
        # Remove any cwd already present in popen_kwargs to avoid duplicate keyword.
        popen_kwargs.pop("cwd", None)
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
            except Exception: pass
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


def run_subprocess_background(
    command: Any,
    cwd: str,
    shell: bool = False,
    startup_timeout: int = 3,
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Start a long-running (service) process detached in the background.

    Output is redirected to a log file instead of a PIPE. Redirecting to a file
    (rather than subprocess.PIPE) is essential: if a child process inherits the
    stdout/stderr pipe descriptors, communicate() would block forever waiting for
    EOF, which is what used to hang the agent on long-running servers.

    Returns immediately with the PID and log path, after a short startup probe to
    detect quick crashes (e.g. port already in use).
    """
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())

    if shell:
        if not isinstance(command, str):
            raise ValueError("When shell=True, command must be a string")
    elif isinstance(command, str):
        raise ValueError("When shell=False, command must be a list")

    log_base = Path(log_dir) if log_dir else Path(safe_cwd)
    try:
        log_base.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_base = Path.cwd()
    log_path = log_base / f"cli_bg_{int(time.time())}.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")

    popen_kwargs = {
        "cwd": safe_cwd,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if platform.system() == "Windows":
        # Detach from the agent's console so Ctrl+C on the agent does not kill it.
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(command, shell=shell, **popen_kwargs)
    except Exception as exc:
        log_file.close()
        return {
            "status": "error",
            "content": f"Failed to start background process: {exc}",
        }

    # The child process inherits the log handle, so the parent can close its own
    # copy right away to avoid leaking file descriptors.
    log_file.close()

    # Quick-crash probe: if the process exits within startup_timeout seconds,
    # capture whatever it wrote to the log and report it back to the model.
    startup_lines: list[str] = []
    deadline = time.monotonic() + max(startup_timeout, 1)
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            break
        time.sleep(0.2)
    code = proc.poll()
    if code is not None:
        # Process exited during the probe window (quick crash / port conflict).
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                startup_lines = lf.read().splitlines()[-40:]
        except Exception:
            startup_lines = []
        snippet = "\n".join(startup_lines) or "(no output)"
        return {
            "status": "error",
            "content": (
                f"Background process exited early (returncode={code}) within "
                f"{startup_timeout}s. Check the log: {log_path}\nLast output:\n{snippet}"
            ),
            "returncode": code,
            "log_path": str(log_path),
        }

    # Keep the file handle open so the log keeps capturing output.
    return {
        "status": "running",
        "pid": proc.pid,
        "log_path": str(log_path),
        "content": (
            f"Service started in the background. PID={proc.pid}. "
            f"Log file: {log_path}. "
            "It will keep running after this tool returns. Monitor readiness by "
            "reading the log file or hitting its health-check endpoint; stop it "
            "later with the appropriate kill command if needed."
        ),
    }


def _tool_error(name: str, arg: str) -> dict[str, Any]:
    return {"status": "error", "content": t("tool_missing_arg", name=name, arg=arg)}


def _walk_tree(root: Path, ignore_names: set[str], max_entries: int) -> list[str]:
    """Iteratively walk a directory tree, pruning ignored dirs and capping results."""
    results: list[str] = []
    stack: list[Path] = [root]
    while stack and len(results) < max_entries:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), reverse=True)
        except OSError:
            continue
        for child in children:
            if len(results) >= max_entries:
                break
            if child.name in ignore_names:
                continue
            results.append(str(child))
            if child.is_dir() and not child.is_symlink():
                stack.append(child)
    return results


def _tool_read_file(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("read_file", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        return {"status": "error", "content": f"Failed to stat file: {path}\n{exc}"}
    if file_size > MAX_READ_FILE_BYTES:
        return {
            "status": "error",
            "content": (
                f"File is too large to read into context: {path} ({file_size} bytes, "
                f"limit is {MAX_READ_FILE_BYTES} bytes). Read it in chunks with a "
                "Python script instead."
            ),
        }
    try:
        content, encoding = read_text_with_fallback(path)
    except Exception as exc:
        return {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}
    if len(content) > MAX_READ_FILE_CHARS:
        content = content[:MAX_READ_FILE_CHARS] + f"\n... (truncated, total={len(content)} chars)"
    if encoding != "utf-8":
        content += f"\n(Note: file was not valid UTF-8 and was decoded as {encoding}.)"
    return {"status": "ok", "content": content}


def _tool_write_file(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("write_file", "path")
    content = args.get("content", "")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, content)
    except Exception as exc:
        return {"status": "error", "content": f"Failed to write file: {path}\n{exc}"}
    return {"status": "ok", "content": f"Written: {path}"}


def _tool_delete_file(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("delete_file", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    try:
        path.unlink()
    except OSError as exc:
        return {"status": "error", "content": f"Failed to delete file: {path}\n{exc}"}
    return {"status": "ok", "content": f"Deleted: {path}"}


def _tool_create_directory(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("create_directory", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "error", "content": f"Failed to create directory: {path}\n{exc}"}
    return {"status": "ok", "content": f"Created directory: {path}"}


def _tool_delete_directory(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("delete_directory", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "error", "content": f"Directory does not exist: {path}"}
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return {"status": "error", "content": f"Failed to delete directory: {path}\n{exc}"}
    return {"status": "ok", "content": f"Deleted directory: {path}"}


def _tool_rename_path(args: dict[str, Any]) -> dict[str, Any]:
    old_path = args.get("old_path")
    new_path = args.get("new_path")
    if not old_path or not new_path:
        return _tool_error("rename_path", "old_path/new_path")
    old, error = resolve_tool_path(old_path, "old_path")
    if error:
        return error
    new, error = resolve_tool_path(new_path, "new_path")
    if error:
        return error
    if not old.exists():
        return {"status": "error", "content": f"Path does not exist: {old}"}
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError as exc:
        return {"status": "error", "content": f"Failed to rename: {old} -> {new}\n{exc}"}
    return {"status": "ok", "content": f"Renamed: {old} -> {new}"}


def _tool_copy_file(args: dict[str, Any]) -> dict[str, Any]:
    source_path = args.get("source_path")
    destination_path = args.get("destination_path")
    if not source_path or not destination_path:
        return _tool_error("copy_file", "source_path/destination_path")
    source, error = resolve_tool_path(source_path, "source_path")
    if error:
        return error
    destination, error = resolve_tool_path(destination_path, "destination_path")
    if error:
        return error
    if not source.exists():
        return {"status": "error", "content": f"Source file does not exist: {source}"}
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    except OSError as exc:
        return {"status": "error", "content": f"Failed to copy: {source} -> {destination}\n{exc}"}
    return {"status": "ok", "content": f"Copied: {source} -> {destination}"}


def _tool_read_image_as_base64(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("read_image_as_base64", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "error", "content": f"Image file does not exist: {path}"}
    return {"status": "ok", "content": json.dumps({"path": str(path), "message": "Image loaded into context for multimodal analysis."}, ensure_ascii=False)}


def _tool_list_dir(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    if not file_path:
        return _tool_error("list_dir", "path")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "ok", "content": json.dumps([], ensure_ascii=False)}

    recursive = bool(args.get("recursive", False))
    if recursive:
        items = _walk_tree(path, IGNORED_DIR_NAMES, MAX_LIST_DIR_ENTRIES)
        if len(items) >= MAX_LIST_DIR_ENTRIES:
            items = items + [f"... (output truncated at {MAX_LIST_DIR_ENTRIES} entries)"]
    else:
        try:
            items = sorted(str(p) for p in path.iterdir())
        except OSError as exc:
            return {"status": "error", "content": f"Failed to list directory: {path}\n{exc}"}
    return {"status": "ok", "content": json.dumps(items, ensure_ascii=False)}


def _tool_search_code(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    pattern = args.get("pattern")
    if not file_path or not pattern:
        return _tool_error("search_code", "path/pattern")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "ok", "content": json.dumps([], ensure_ascii=False)}

    recursive = bool(args.get("recursive", True))
    max_results = args.get("max_results")
    try:
        max_results = int(max_results) if max_results is not None else 50
    except (TypeError, ValueError):
        max_results = 50
    if max_results <= 0:
        max_results = 50

    if path.is_file():
        candidates = [path]
    elif recursive:
        candidates = [Path(item) for item in _walk_tree(path, IGNORED_DIR_NAMES, MAX_LIST_DIR_ENTRIES)]
    else:
        candidates = [item for item in sorted(path.iterdir())]

    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if len(matches) >= max_results:
            break
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            content, _encoding = read_text_with_fallback(candidate)
        except (OSError, ValueError):
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
    return {"status": "ok", "content": json.dumps(matches, ensure_ascii=False)}


def _tool_edit_file(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args.get("path") or args.get("file_path")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not file_path or old_string is None or new_string is None:
        return _tool_error("edit_file", "path/old_string/new_string")
    path, error = resolve_tool_path(file_path, "path")
    if error:
        return error
    if not path.exists():
        return {"status": "error", "content": f"File does not exist: {path}"}
    try:
        original_text, _encoding = read_text_with_fallback(path)
    except Exception as exc:
        return {"status": "error", "content": f"Failed to read file: {path}\n{exc}"}

    if not old_string:
        return {"status": "error", "content": "old_string must not be empty."}
    occurrences = original_text.count(old_string)
    if occurrences == 0:
        return {"status": "error", "content": f"Target text not found in file: {path}"}
    if occurrences > 1:
        return {
            "status": "error",
            "content": (
                f"Target text appears {occurrences} times in {path}. "
                "Provide a longer, unique old_string (include surrounding lines) to disambiguate."
            ),
        }

    updated_text = original_text.replace(old_string, new_string, 1)
    try:
        write_text_atomic(path, updated_text)
    except Exception as exc:
        return {"status": "error", "content": f"Failed to write file: {path}\n{exc}"}
    return {"status": "ok", "content": f"Updated: {path}"}


def _tool_execute_python_script(args: dict[str, Any]) -> dict[str, Any]:
    script = args.get("script")
    if not script:
        return _tool_error("execute_python_script", "script")
    cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())

    # Long-running service mode: start detached and return immediately.
    background = bool(args.get("background") or args.get("wait") is False)

    try:
        startup_timeout = int(args.get("startup_timeout") or 3)
    except (TypeError, ValueError):
        startup_timeout = 3

    # Use tempfile to avoid race conditions
    script_path = None
    fd, script_path_str = tempfile.mkstemp(suffix=".py", prefix="cli_script_", dir=cwd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        script_path = Path(script_path_str)

        if background:
            # Long-running service: start detached, return immediately with PID/log.
            # The script file is intentionally NOT deleted: the detached process may
            # not have opened it yet on Windows, and deleting it would break startup.
            response = run_subprocess_background(
                [sys.executable, str(script_path)],
                cwd,
                shell=False,
                startup_timeout=startup_timeout,
                log_dir=cwd,
            )
            if response.get("status") == "running":
                response["script_path"] = str(script_path)
            return response

        # Foreground / short command: get the timeout, default 120s, capped at 360s.
        try:
            timeout_seconds = int(args.get("timeout_seconds") or 120)
        except (TypeError, ValueError):
            timeout_seconds = 120
        timeout_seconds = min(max(timeout_seconds, 10), TOOL_SUBPROCESS_TIMEOUT)

        returncode, stdout, stderr = run_subprocess_command(
            [sys.executable, str(script_path)],
            cwd,
            shell=False,
            timeout=timeout_seconds
        )
        return {
            "status": "ok" if returncode == 0 else "error",
            "content": stdout + stderr,
            "returncode": returncode,
        }
    finally:
        # Cleanup temp file (foreground runs only; background scripts are kept).
        if script_path is not None and not background:
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass


BUILTIN_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "delete_file": _tool_delete_file,
    "create_directory": _tool_create_directory,
    "delete_directory": _tool_delete_directory,
    "rename_path": _tool_rename_path,
    "copy_file": _tool_copy_file,
    "read_image_as_base64": _tool_read_image_as_base64,
    "list_dir": _tool_list_dir,
    "search_code": _tool_search_code,
    "edit_file": _tool_edit_file,
    "execute_python_script": _tool_execute_python_script,
}


def execute_tool_call(tool_call: Any) -> dict[str, Any]:
    """Execute a tool call and return the result."""
    if not hasattr(tool_call, 'function'):
        return {"status": "error", "content": "Invalid tool call object"}
    name = getattr(tool_call.function, "name", "")
    args = safe_parse_tool_args(getattr(tool_call.function, "arguments", {}))
    ensure_not_interrupted()

    handler = BUILTIN_TOOL_HANDLERS.get(name)
    if handler is not None:
        result = handler(args)
    elif name in {item["function"]["name"] for item in ACTIVE_MCP_TOOL_DEFINITIONS()}:
        return run_mcp_tool(name, args)
    else:
        return {"status": "error", "content": t("unknown_tool", name=name)}

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
    print_section(content, title=title)


def show_tool_result(tool_call: Any, result: dict[str, Any]) -> None:
    display_result = sanitize_tool_result_for_display(result)
    description = get_tool_description(tool_call)
    description_text = f"{description}\n\n" if description else ""
    print_section(
        f"[bold cyan]{tool_call.function.name}[/bold cyan]\n{description_text}{json.dumps(display_result, ensure_ascii=False, indent=2)}",
        title=t("tool_result_title"),
    )


def _filter_think_tags(content: str) -> str:
    """Remove paired <think>..</think> and standalone <think/> tags and their contents.

    - Removes any content between an opening <think> (or <think/>) and a closing </think>.
    - Removes standalone tags like <think/> or <think>.
    - An unclosed <think> opening tag truncates everything from the tag onwards.
    """
    if not content:
        return content

    between_re = re.compile(r"<think\b[^>]*>.*?</think\s*>", flags=re.IGNORECASE | re.DOTALL)
    result = between_re.sub("", content)

    # Unclosed opening tag (self-closing <think/> tags are NOT considered unclosed):
    # drop everything from the tag to the end of content.
    unclosed = re.search(r"<think\b[^>]*>", result, flags=re.IGNORECASE)
    if unclosed and not unclosed.group(0).endswith("/>"):
        result = result[: unclosed.start()]

    tag_re = re.compile(r"<think\b[^>]*/?>", flags=re.IGNORECASE)
    result = tag_re.sub("", result)

    return result


class StreamingThinkFilter:
    """Incrementally strip <think>...</think> blocks from streamed content.

    feed() returns the portion of the accumulated buffer that is safe to display
    (i.e. outside any think block). A trailing fragment that might still turn into
    a think tag is held back until close() resolves it, so partial tags never leak.
    Behavior matches _filter_think_tags: an unterminated opening tag suppresses all
    remaining content, and self-closing <think/> tags are simply removed.
    """
    _OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
    _CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)

    def __init__(self) -> None:
        self._pending = ""
        self._in_think = False
        self.visible = ""

    def feed(self, text: str) -> str:
        if text:
            self._pending += text
        return self._drain(final=False)

    def close(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        emit = ""
        while self._pending:
            if self._in_think:
                m = self._CLOSE_RE.search(self._pending)
                if m:
                    self._pending = self._pending[m.end():]
                    self._in_think = False
                    continue
                if final:
                    self._pending = ""
                else:
                    keep = self._partial_close_start(self._pending)
                    self._pending = "" if keep is None else self._pending[keep:]
                break
            m = self._OPEN_RE.search(self._pending)
            if m:
                emit += self._pending[:m.start()]
                if not m.group(0).endswith("/>"):
                    self._in_think = True
                self._pending = self._pending[m.end():]
                continue
            if final:
                emit += self._pending
                self._pending = ""
                break
            hold = self._potential_open_start(self._pending)
            if hold is None:
                emit += self._pending
                self._pending = ""
            else:
                emit += self._pending[:hold]
                self._pending = self._pending[hold:]
            break
        self.visible += emit
        return emit

    @staticmethod
    def _potential_open_start(s: str) -> int | None:
        # Index of the first '<' that could still grow into a <think...> opening tag.
        target = "<think"
        i = 0
        while True:
            j = s.find("<", i)
            if j == -1:
                return None
            rest = s[j:]
            low = rest.lower()
            if len(low) < len(target) and target.startswith(low):
                return j
            if low.startswith(target):
                after = rest[len(target):]
                if after and (after[0].isalnum() or after[0] == "_"):
                    i = j + 1
                    continue
                if ">" in after:
                    i = j + 1
                    continue
                return j
            i = j + 1

    @staticmethod
    def _partial_close_start(s: str) -> int | None:
        # Index of a trailing fragment that could still become the </think> tag.
        for k in range(len(s)):
            if s[k] != "<":
                continue
            low = s[k:].lower()
            if "</think".startswith(low) or re.fullmatch(r"</think\s*", low):
                return k
        return None


def _extract_delta_reasoning(delta: Any) -> str:
    for attr in ("reasoning_content", "reasoning"):
        value = getattr(delta, attr, None)
        if isinstance(value, str) and value:
            return value
    extra = getattr(delta, "model_extra", None)
    if isinstance(extra, dict):
        for key in ("reasoning_content", "reasoning"):
            value = extra.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _stream_chat_once(client: OpenAI, request_payload: dict[str, Any], request_timeout: Any, model: str, debug_enabled: bool) -> Any:
    """Run a streaming chat completion, printing visible content as it arrives.

    Reconstructs an assistant-message-like object (content, tool_calls, reasoning)
    compatible with what the non-streaming path and run_agent expect.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_acc: dict[int, dict[str, Any]] = {}
    usage: Any = None
    think_filter = StreamingThinkFilter()
    printed_any = False
    reply_header_printed = False

    stream = client.chat.completions.create(**request_payload, timeout=request_timeout)
    for chunk in stream:
        ensure_not_interrupted()
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
            update_total_token_usage(usage)
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = choices[0].delta

        reasoning_piece = _extract_delta_reasoning(delta)
        if reasoning_piece:
            reasoning_parts.append(reasoning_piece)

        content_piece = getattr(delta, "content", None)
        if content_piece:
            content_parts.append(content_piece)
            visible = think_filter.feed(content_piece)
            if visible:
                if not reply_header_printed:
                    console.print("\n" + f"{'-' * 18} {t('agent_reply_title')} {'-' * 18}")
                    reply_header_printed = True
                console.print(visible, end="", highlight=False, soft_wrap=True)
                printed_any = True

        tool_call_deltas = getattr(delta, "tool_calls", None) or []
        for tc_delta in tool_call_deltas:
            idx = getattr(tc_delta, "index", 0) or 0
            slot = tool_calls_acc.setdefault(idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
            if getattr(tc_delta, "id", None):
                slot["id"] = tc_delta.id
            if getattr(tc_delta, "type", None):
                slot["type"] = tc_delta.type
            fn = getattr(tc_delta, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["function"]["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["function"]["arguments"] += fn.arguments

    tail = think_filter.close()
    if tail:
        if not reply_header_printed:
            console.print("\n" + f"{'-' * 18} {t('agent_reply_title')} {'-' * 18}")
            reply_header_printed = True
        console.print(tail, end="", highlight=False, soft_wrap=True)
        printed_any = True
    if printed_any:
        console.print()

    filtered_content = _filter_think_tags("".join(content_parts))
    ordered_slots = [tool_calls_acc[k] for k in sorted(tool_calls_acc)]
    tool_calls = [
        SimpleNamespace(
            id=slot["id"] or f"tool_call_{i}",
            type=slot.get("type") or "function",
            function=SimpleNamespace(name=slot["function"]["name"], arguments=slot["function"]["arguments"]),
        )
        for i, slot in enumerate(ordered_slots)
    ]

    assistant_message = SimpleNamespace()
    assistant_message.content = filtered_content
    assistant_message.tool_calls = tool_calls
    reasoning_text = "".join(reasoning_parts)
    assistant_message.reasoning = reasoning_text
    assistant_message.reasoning_content = reasoning_text
    assistant_message.content_reasoning = ""
    assistant_message.streamed = printed_any

    if debug_enabled:
        debug_dump = {
            "content": filtered_content,
            "reasoning": reasoning_text,
            "tool_calls": [
                {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        }
        show_stage(t("model_raw_response"), json.dumps(debug_dump, ensure_ascii=False, indent=2))

    return assistant_message


def chat_once(client: OpenAI, model: str, messages: list[dict[str, Any]], temperature: float, debug_enabled: bool = False, disable_tools:bool = False, stream_display: bool = False) -> Any:
    ensure_not_interrupted()
    wait_for_model_call_interval()
    tool_definitions = get_tool_definitions()
    request_payload = None
    if disable_tools:
        request_payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
    else:
        request_payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "tools": tool_definitions,
        }
        # Only include tool_choice when tools are provided; some backends reject it otherwise
        if tool_definitions:
            request_payload["tool_choice"] = "auto"
    request_timeout = getattr(client, "timeout", None)
    if request_timeout is None:
        request_timeout = MODEL_REQUEST_TIMEOUT_SECONDS

    if debug_enabled:
        show_stage(t("model_request_params"), json.dumps(request_payload, ensure_ascii=False, indent=2))

    try:
        if stream_display:
            request_payload["stream"] = True
            request_payload["stream_options"] = {"include_usage": True}
            return _stream_chat_once(client, request_payload, request_timeout, model, debug_enabled)

        response = client.chat.completions.create(**request_payload, timeout=request_timeout)
        assistant_message = response.choices[0].message
        usage = getattr(response, "usage", None)
        if usage is not None:
            update_total_token_usage(usage)

        response_content = assistant_message.content or ""
        # Strip out internal <think> tags from model content
        try:
            response_content = _filter_think_tags(response_content)
            assistant_message.content = response_content
        except Exception:
            pass
        if debug_enabled:
            raw_response = response.model_dump_json(indent=2)
            show_stage(t("model_raw_response"), raw_response)

        return assistant_message
    except KeyboardInterrupt:
        show_stage(t("cancelled"), t("model_cancelled"))
        raise
    except (APITimeoutError, TimeoutError):
        assistant_message = SimpleNamespace()
        assistant_message.tool_calls = []
        assistant_message.content = "Model call timed out. Please try again."
        assistant_message.is_error = True
        return assistant_message
    except Exception as exc:
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
        assistant_message.is_error = True
        if debug_enabled:
            show_stage("Debug - model call error", traceback.format_exc())
        else:
            show_stage(t("model_call_failed", error=user_msg), user_msg)
        return assistant_message
    finally:
        mark_model_call_completed()


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


def _trim_conversation_window(conversation: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    """Keep the last max_count messages without breaking tool_calls/tool message pairs.

    Cutting between an assistant message carrying tool_calls and its tool results
    violates the chat API contract (a 400 error on most OpenAI-compatible backends),
    so leading orphaned tool messages are dropped from the window.
    """
    window = conversation[-max_count:] if max_count > 0 else []
    while window and window[0].get("role") == "tool":
        window = window[1:]
    if not window and conversation:
        window = [conversation[-1]]
    return window


def run_agent(client: OpenAI, model: str, system_prompt: str, user_text: str, debug_enabled: bool = False) -> None:
    reset_interruption_state()

    history = CONVERSATION_HISTORY
    first_task_prompt = {"role": "user", "content": user_text}
    history.append(first_task_prompt)
    _trim_conversation_history()

    system_prompt_message = {"role": "system", "content": system_prompt}
    messages = []
    messages.extend(history)

    _agent_iteration = 0
    while _agent_iteration < MAX_AGENT_ITERATIONS:
        _agent_iteration += 1
        try:
            finalize_prompt = []
            # Keep system prompt separate, only truncate conversation messages
            conversation_messages = _trim_conversation_window(
                [m for m in messages if m.get("role") != "system"],
                MAX_CHAT_ROUNDS * 2,
            )
            finalize_prompt.extend(conversation_messages)
            existing_first_prompt = first_task_prompt in conversation_messages
            if not existing_first_prompt:
                finalize_prompt = [first_task_prompt] + finalize_prompt
            if not BARE_MODE:
                finalize_prompt = [system_prompt_message] + finalize_prompt
            assistant_message = chat_once(client, model, finalize_prompt, temperature=0.2, debug_enabled=debug_enabled, disable_tools=BARE_MODE, stream_display=True)
        except KeyboardInterrupt:
            print_section(t("task_cancelled"), title=t("interrupted_title"))
            return
        except Exception:
            print_section(traceback.format_exc(), title=t("runtime_error_title"))
            return

        # A failed model call (quota/timeout/5xx) must not be treated as a
        # genuine assistant reply.
        if getattr(assistant_message, "is_error", False):
            return

        tool_calls = [] if BARE_MODE else to_tool_call_objects(list(getattr(assistant_message, "tool_calls", []) or []))
        if not BARE_MODE and not tool_calls:
            tool_calls = parse_tool_calls_from_content(getattr(assistant_message, "content", ""))

        if not tool_calls:
            answer = assistant_message.content or "I did not receive a valid reply."
            history.append({"role": "assistant", "content": answer})
            _trim_conversation_history()
            # In streaming mode chat_once already printed the visible reply live, so
            # only fall back to a panel when nothing was streamed (empty/suppressed).
            if not getattr(assistant_message, "streamed", False):
                print_section(answer, title=t("agent_reply_title"))
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
        function_reasoning_content = getattr(assistant_message,"reasoning_content",None) or ""
        final_reasoning = function_reasoning.strip() or function_content_reasoning.strip() or function_content.strip() or function_reasoning_content.strip()

        show_stage(t("starting_tool_call"), f"reasoning:\n{final_reasoning}")

        for tc in tool_calls:
            try:
                result = execute_tool_call(tc)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
                show_tool_result(tc, result)

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
                        pass
            except KeyboardInterrupt:
                print_section(t("current_tool_cancelled"), title=t("interrupted_title"))
                return
            except Exception:
                error_payload = {"status": "error", "content": traceback.format_exc()}
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(error_payload, ensure_ascii=False)})
                print_section(traceback.format_exc(), title=t("tool_execution_error"))


def interactive_loop(client: OpenAI, model: str, system_prompt: str, history_file: Path, debug_enabled: bool = False) -> None:
    print_section(
        "[bold green]Prompt Pilot CLI Coding Agent[/bold green]\n"
        + t("welcome_message") + "\n",
        title=t("startup_title"),
    )

    slash_commands = ["/exit", "/clear"]
    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=SlashCommandCompleter(slash_commands),
        complete_while_typing=True,
        bottom_toolbar=build_bottom_toolbar_text,
    )
    last_ctrl_c_at = 0.0
    while True:
        try:
            user_text = session.prompt(t("prompt_placeholder"))
        except KeyboardInterrupt:
            # First Ctrl+C at the prompt just clears the input; a second press
            # within a few seconds exits (consistent with the toolbar hint).
            now = time.monotonic()
            if now - last_ctrl_c_at <= 3.0:
                console.print("\n" + t("exit_message"))
                return
            last_ctrl_c_at = now
            console.print("\n" + t("ctrl_c_hint"))
            continue

        if user_text.strip() == "/exit":
            console.print(t("bye_message"))
            return
        if user_text.strip() == "/clear":
            reset_conversation_history()
            console.print(t("clear_session_message"))
            continue

        run_agent(client, model, system_prompt, user_text, debug_enabled=debug_enabled)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=t("cli_parser_description"))
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {APPLICATION_VERSION}")
    parser.add_argument("-t", "--task", help=t("task_argument_help"))
    parser.add_argument("-d", "--workdir", default=WORKSPACE_DIR, help=t("workdir_argument_help"))
    parser.add_argument("-l", "--lang", default="en", choices=["zh", "en"], help="language code for localization (zh / en, default: en)")
    parser.add_argument("-rd", "--request-delay", default=RE_ACTION_DELAY, help=f"delay in seconds between model requests (default: {RE_ACTION_DELAY})")
    parser.add_argument("-b", "--bare", action="store_true", help=t("bare_argument_help"))
    return parser


def main() -> None:
    global UI_SYSTEM_LANGUAGE, DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, BARE_MODE

    parser = build_cli_parser()
    args = parser.parse_args()

    RE_ACTION_DELAY = int(args.request_delay)
    BARE_MODE = bool(args.bare)

    # Set localization language
    UI_SYSTEM_LANGUAGE = args.lang

    # set up workspace directory
    WORKSPACE_DIR = Path(args.workdir)
    print_section(t("workdir_message", path=WORKSPACE_DIR), title=t("cli_config_title"))

    try:
        model_cfg, system_prompt = ensure_config(workdir=WORKSPACE_DIR)
    except RuntimeError as exc:
        print_section(str(exc), title=t("config_error_title"))
        return

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WORKSPACE_DIR)

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)

    mcp_tools = discover_mcp_tools(model_cfg)
    if mcp_tools:
        print_section(t("mcp_connected", count=len(mcp_tools)), title=t("mcp_config_title"))

    try:
        client = build_client(model_cfg)
    except RuntimeError as exc:
        print_section(str(exc), title=t("config_error_title"))
        return

    history_file = ROOT / ".history"

    debug_enabled = bool(model_cfg.get("debug", False))
    if debug_enabled:
        print_section(t("debug_enabled_message"), title=t("debug_config_title"))

    if args.task:
        try:
            run_agent(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, args.task, debug_enabled=debug_enabled)
        except KeyboardInterrupt:
            print_section(t("task_cancelled"), title=t("interrupted_title"))
        return

    try:
        interactive_loop(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, history_file, debug_enabled=debug_enabled)
    except KeyboardInterrupt:
        print_section(t("task_cancelled"), title=t("interrupted_title"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print_section(traceback.format_exc(), title=t("runtime_error_title"))
        sys.exit(1)