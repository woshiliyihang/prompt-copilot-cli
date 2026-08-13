from __future__ import annotations
from openai import OpenAI
from pathlib import Path
from rich.console import Console
from typing import Any
import json
import logging

ROOT = Path.home() / ".prompt-copilot"

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
    "execute_command_desc": {"zh": "在指定目录下执行 shell 命令。", "en": "Execute a shell command in the specified directory."},
    "execute_python_script_desc": {"zh": "执行一个 Python 脚本或一段 Python 代码。", "en": "Execute a Python script or a block of Python code."},
    "path_desc": {"zh": "要读取的文件路径。", "en": "Path of the file to read."},
    "write_path_desc": {"zh": "要写入的目标文件路径。", "en": "Target file path to write to."},
    "content_desc": {"zh": "要写入的文件内容。", "en": "Content to write to the file."},
    "list_dir_path_desc": {"zh": "要列出的目录路径。", "en": "Directory path to list."},
    "list_dir_recursive_desc": {"zh": "是否递归遍历子目录。", "en": "Whether to recursively traverse subdirectories."},
    "command_desc": {"zh": "要执行的命令。", "en": "Command to execute."},
    "cwd_desc": {"zh": "执行命令的工作目录。", "en": "Working directory for the command."},
    "script_desc": {"zh": "要执行的脚本内容。", "en": "Script content to execute."},
    "script_cwd_desc": {"zh": "脚本执行工作目录。", "en": "Working directory for the script execution."},
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
    "tool_result": {"zh": "{name} 结果：{result}", "en": "{name} result: {result}"},
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
    "tool_call_finished": {"zh": "调用工具结束", "en": "Tool call finished"},
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
    "task_end_user_message": {"zh": "对话记录（从 /task-start 开始，按时间从旧到新）：\n\n{compiled_text}", "en": "Conversation history (starting from /task-start, from oldest to newest):\n\n{compiled_text}"},
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

ACTIVE_MCP_TOOL_CONFIG: dict[str, Any] = {}

ACTIVE_MCP_TOOL_CONFIGS: list[dict[str, Any]] = []

ACTIVE_MCP_TOOL_SERVER_BY_NAME: dict[str, dict[str, Any]] = {}

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
