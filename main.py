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
import threading
import time
import traceback
import urllib.request
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
        return "No token usage information"

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    return (
        f"{"prompt"}={prompt_tokens if prompt_tokens is not None else '-'} | "
        f"{"completion"}={completion_tokens if completion_tokens is not None else '-'} | "
        f"{"total"}={total_tokens if total_tokens is not None else '-'}"
    )


def format_cumulative_token_summary() -> str:
    return (
        f"{"Cumulative tokens"} "
        f"{"prompt"}={TOTAL_TOKEN_USAGE['prompt_tokens']} | "
        f"{"completion"}={TOTAL_TOKEN_USAGE['completion_tokens']} | "
        f"{"total"}={TOTAL_TOKEN_USAGE['total_tokens']}"
    )


def build_bottom_toolbar_text() -> str:
    base = "Available commands: /exit /clear /task-start /task-end | Tab completion | Ctrl+C can interrupt the current execution"
    token_summary = format_cumulative_token_summary()
    return f"{token_summary} | {base}"




ZH_SYSTEM_PROMPT = """
你是一个基于 CLI 的编程 Agent，你的名字是 Jason Li，专注于使用文件、命令、Python 脚本等工具完成开发任务。

工作原则：
1、先检查工作目录并理解需求。
2、优先使用网络搜索类工具搜索网络实时信息，例如：天气、新闻、资讯等等。
3、所有代码文件生成、编辑、删除等操作均在工作目录中执行。如果用户没有指定具体目录，默认工作目录为工程的根目录。
4、如果需要，输出简洁的说明与下一步建议。
5、若涉及删除系统文件或执行危险命令，必须先向用户确认后方可执行。
6、当用户输入指令：/task-start 的时候,直接回复：请输入你的第一条初始提示。
7、遇到信息盲区时，严禁主观臆测，请优先使用搜索工具补齐信息缺口，确保回答精准有据。
8、如果用户想“读取/识别其他二进制文件”，你必须明确告知：我无法直接读取或理解通用二进制文件内容。
9、如果用户想“查看图片内容”，应优先调用图片读取工具将图片转成 base64 或视觉消息格式，再将其发送给支持多模态的模型。
10、对于图片类任务，优先使用图片工具而不是尝试直接读取二进制原始内容。
11、当用户输入一条任务指令时，如果是详细的任务清单，你就理解用户要求逐步完成工作，同时每个步骤执行完毕要反馈状态和进度。
12、使用 execute_python_script 时必须在脚本内自行管理进程生命周期：若是 ls、pytest、build 等短命令用 subprocess.run 显式设置 timeout，若是 npm run dev、flask run 等常驻服务用 subprocess.Popen 脱离终端启动并在脚本内轮询健康检查 URL，确认就绪后打印 PID 并立即 sys.exit() 退出以防触发 360 秒强制超时。
13、无法调用官方联网检索工具而通过脚本抓取网络内容时需区分数据类型处理，抓取普通网页文本需剔除 HTML 标签、样式代码、广告碎片、无效注释、多余空行等冗余内容，仅留存有效正文且文本超出 8000 字符则截断，文本输出上限为 8000 字符，抓取程序源代码则无需过滤，完整保留原始代码、自带注释、缩进换行与原有格式，代码输出无字符数量限制。

"""

PLANNING_PROMPT = """
你是一个基于 CLI 的编程 Agent，你的名字是 Jason Li，专注于使用文件、命令、Python 脚本等工具完成开发任务。

在生成任务执行计划时，请遵循以下工作原则：

1、先检查工作目录并理解需求。
2、优先使用网络搜索类工具搜索网络实时信息，例如：天气、新闻、资讯等等。
3、所有代码文件生成、编辑、删除等操作均在工作目录中执行。如果用户没有指定具体目录，默认工作目录为工程的根目录。
4、如果需要，输出简洁的说明与下一步建议。
5、若涉及删除系统文件或执行危险命令，必须先向用户确认后方可执行。
6、当用户输入指令：/task-start 的时候,直接回复：请输入你的第一条初始提示。
7、遇到信息盲区时，严禁主观臆测，请优先使用搜索工具补齐信息缺口，确保回答精准有据。
8、如果用户想“读取/识别其他二进制文件”，你必须明确告知：我无法直接读取或理解通用二进制文件内容。
9、如果用户想“查看图片内容”，应优先调用图片读取工具将图片转成 base64 或视觉消息格式，再将其发送给支持多模态的模型。
10、对于图片类任务，优先使用图片工具而不是尝试直接读取二进制原始内容。
11、当用户输入一条任务指令时，如果是详细的任务清单，你就理解用户要求逐步完成工作，同时每个步骤执行完毕要反馈状态和进度。
12、使用 execute_python_script 时必须在脚本内自行管理进程生命周期：若是 ls、pytest、build 等短命令用 subprocess.run 显式设置 timeout，若是 npm run dev、flask run 等常驻服务用 subprocess.Popen 脱离终端启动并在脚本内轮询健康检查 URL，确认就绪后打印 PID 并立即 sys.exit() 退出以防触发 360 秒强制超时。
13、无法调用官方联网检索工具而通过脚本抓取网络内容时需区分数据类型处理，抓取普通网页文本需剔除 HTML 标签、样式代码、广告碎片、无效注释、多余空行等冗余内容，仅留存有效正文且文本超出 8000 字符则截断，文本输出上限为 8000 字符，抓取程序源代码则无需过滤，完整保留原始代码、自带注释、缩进换行与原有格式，代码输出无字符数量限制。

请严格按照以下格式输出结果：

用户原始指令：......

结合上下文得到用户的完整意图：.....

接下来按照这个步骤逐步执行完成任务：
1、第一步：......
2、第二步：......
......

"""





APPLICATION_VERSION = "0.2.2"
DEFAULT_SYSTEM_PROMPT = ZH_SYSTEM_PROMPT
WORKSPACE_DIR = ROOT / "workspace"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "agent_runtime.log"
DEFAULT_MAX_CHAT_COUNT = 7
CHAT_MESSAGE_MAX_COUNT = 8
CONFIG_SAVE_FILE_PATH = ROOT / "config.json"
RE_ACTION_DELAY = 1 # unit: seconds
TOOL_SUBPROCESS_TIMEOUT = 6 * 60  # 1 hour in seconds
MODEL_REQUEST_TIMEOUT_SECONDS = 600  # generous timeout for slower model generations
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
    "model": "Model name, for example qwen2.5-7b-instruct",
    "base_url": "OpenAI-compatible base URL, for example http://127.0.0.1:11434/v1",
    "api_key": "API key; for a local model you can enter any non-empty string",
    "temperature": "Sampling temperature, usually between 0.0 and 1.0",
    "debug": "Whether to enable debug logging; true is recommended",
    "mcp": "MCP tool-server configuration object containing enabled and servers",
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
            "description": "Read the contents of the specified file in text mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite the content of the specified file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path to write to."},
                    "content": {"type": "string", "description": "Content to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete the specified file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory (supports recursive creation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": "Delete a directory and its contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_path",
            "description": "Rename or move a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Path of the file to read."},
                    "new_path": {"type": "string", "description": "Target file path to write to."}
                },
                "required": ["old_path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Path of the file to read."},
                    "destination_path": {"type": "string", "description": "Target file path to write to."}
                },
                "required": ["source_path", "destination_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_image_as_base64",
            "description": "Read an image file and encode it as base64.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file to read."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and subdirectories in the given directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list."},
                    "recursive": {"type": "boolean", "description": "Whether to recursively traverse subdirectories."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_script",
            "description": "Executes a Python script or a block of Python code. The script runs in an isolated process. Standard input is disabled to prevent hanging. If the script runs longer than the timeout, it will be forcefully terminated along with any child processes it spawned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "The Python script content to execute."},
                    "cwd": {"type": "string", "description": "Working directory for the script execution."},
                    "timeout_seconds": {"type": "integer", "description": "Optional. Timeout in seconds. Defaults to 360."}
                },
                "required": ["script", "cwd"],
            },
        },
    },
]
ACTIVE_MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_CONFIG: dict[str, Any] = {}
ACTIVE_MCP_TOOL_CONFIGS: list[dict[str, Any]] = []
ACTIVE_MCP_TOOL_SERVER_BY_NAME: dict[str, dict[str, Any]] = {}
INTERRUPTION_REQUESTED = False


def handle_sigint(signum: int, frame: Any) -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = True
    raise KeyboardInterrupt("Interrupted by user")


def ensure_not_interrupted() -> None:
    if INTERRUPTION_REQUESTED:
        raise KeyboardInterrupt("Interrupted by user")


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
        return output or "Not detected"
    except Exception:
        return "Not detected"


def build_device_environment_context(workdir: str) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os_name = platform.system() or t("unknown") if False else platform.system() or 'Unknown'
    os_release = platform.release() or 'Unknown'
    os_version = platform.version() or 'Unknown'
    os_arch = platform.machine() or 'Unknown'
    python_version = platform.python_version() or sys.version.split()[0]
    node_path = shutil.which('node')
    node_version = get_version_from_command(['node', '--version']) if node_path else "Not detected"
    npm_version = get_version_from_command(['npm', '--version']) if shutil.which('npm') else "Not detected"

    return (
        "Device environment:" + "\n"
        + "Current device time:" + now + "\n"
        + "Current operating system info:"
        + f"system={os_name}, release={os_release}, version={os_version}, arch={os_arch}\n"
        + "Current software environment info:"
        + f"python={python_version}, node={node_version}, npm={npm_version}\n"
        + "Current working directory:" + workdir
    )


def safe_parse_tool_args(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.warning("Tool arguments are not valid JSON. Raw content: %s", raw_arguments)
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
    lines = ["Configuration field descriptions:"]
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
            "Failed to read config file. Please check {path}: {error}".format(path=CONFIG_SAVE_FILE_PATH, error=exc) + "\n\n"
            + f"{_format_config_field_help()}"
        ) from exc

    if not isinstance(file_payload, dict):
        raise RuntimeError(
            "Config file format is invalid. Expected a JSON object: {path}".format(path=CONFIG_SAVE_FILE_PATH) + "\n\n"
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
            "The model configuration is incomplete. Please complete the required fields in {path} before using the agent.
Missing fields: {fields}".format(path=CONFIG_SAVE_FILE_PATH, fields=', '.join(missing_fields)) + "\n\n"
            + f"{_format_config_field_help()}"
        )

    device_prompt = build_device_environment_context(str(workdir))
    system_prompt = device_prompt + "\n\n" + DEFAULT_SYSTEM_PROMPT
    return DEFAULT_MODEL_CONFIG, system_prompt


def build_client(model_cfg: dict[str, Any]) -> OpenAI:
    api_key = model_cfg.get("api_key")
    if not api_key:
        raise RuntimeError("API key is not configured. Please fill in the api_key field in the config file first.")
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
                logger.exception("Failed to discover MCP tools" + f", server={server_config.get('name')}")
                console.print(Panel.fit("MCP tool unavailable: {name}; it will be ignored.".format(name=server_config.get("name") or str(server_config)), title="Failed to discover MCP tools"))

        ACTIVE_MCP_TOOL_DEFINITIONS = definitions
        ACTIVE_MCP_TOOL_CONFIGS = active_configs
        ACTIVE_MCP_TOOL_CONFIG = active_configs[0] if active_configs else {}
        logger.info("Discovered MCP tool definitions: %s", [item["function"]["name"] for item in definitions])
        return definitions
    except Exception:
        logger.exception("Failed to discover MCP tools")
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []


def run_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_config = ACTIVE_MCP_TOOL_SERVER_BY_NAME.get(name) or ACTIVE_MCP_TOOL_CONFIGS[0] or ACTIVE_MCP_TOOL_CONFIG
    if not server_config:
        return {"status": "error", "content": "No MCP server configuration found for tool: {name}".format(name=name)}

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
        logger.exception("Failed to execute MCP tool" + f", tool={name}")
        return {"status": "error", "content": traceback.format_exc()}


def looks_like_background_service_command(command: Any) -> bool:
    if isinstance(command, (list, tuple)):
        text = " ".join(str(part) for part in command)
    else:
        text = str(command or "")

    normalized = text.lower().strip()
    if not normalized:
        return False

    background_markers = (
        "npm run dev",
        "npm start",
        "pnpm dev",
        "pnpm start",
        "yarn dev",
        "yarn start",
        "vite",
        "next dev",
        "next start",
        "uvicorn",
        "flask run",
        "gunicorn",
        "python app.py",
        "python main.py",
        "python manage.py runserver",
        "python -m http.server",
        "http.server",
        "serve",
        "watch",
    )

    if any(marker in normalized for marker in background_markers):
        return True

    if normalized.startswith(("python ", "py ")) and any(script in normalized for script in ("app.py", "main.py", "manage.py")):
        return True

    return False


def _read_text_file_tail(path: str | os.PathLike[str], max_chars: int = 4000) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception:
        return ""


def stream_background_process_output(process: subprocess.Popen[Any], log_path: str | os.PathLike[str]) -> None:
    log_file = Path(log_path)

    def _watch_output() -> None:
        last_position = 0
        try:
            if log_file.exists():
                last_position = log_file.stat().st_size
        except Exception:
            last_position = 0

        while process.poll() is None:
            try:
                if log_file.exists():
                    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(last_position)
                        chunk = handle.read()
                        if chunk:
                            last_position = handle.tell()
                            if chunk.strip():
                                console.print(chunk.rstrip(), style="dim")
            except Exception:
                pass
            time.sleep(0.2)

        try:
            if log_file.exists():
                with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(last_position)
                    chunk = handle.read()
                    if chunk and chunk.strip():
                        console.print(chunk.rstrip(), style="dim")
        except Exception:
            pass

    threading.Thread(target=_watch_output, daemon=True).start()


def start_background_process(command: Any, cwd: str, timeout_seconds: int | None = None, output_log_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    log_path = Path(output_log_path).expanduser() if output_log_path else ROOT / "logs" / "background" / f"cmd_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    startup_kwargs: dict[str, Any] = {
        "cwd": safe_cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }

    if os.name == "nt":
        startup_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        if isinstance(command, (list, tuple)):
            process = subprocess.Popen(list(command), start_new_session=True, **startup_kwargs)
        else:
            process = subprocess.Popen(str(command), shell=True, start_new_session=True, **startup_kwargs)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("Failed to start subprocess in cwd=%s, falling back to %s: %s", cwd, safe_cwd, exc)
        return {"status": "error", "content": str(exc)}

    def _drain_output() -> None:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                while True:
                    chunk = process.stdout.readline() if process.stdout is not None else ""
                    if chunk == "":
                        break
                    handle.write(chunk)
                    handle.flush()
        except Exception:
            pass

    output_thread = threading.Thread(target=_drain_output, daemon=True)
    output_thread.start()

    if timeout_seconds is not None and timeout_seconds > 0:
        def _watch_timeout() -> None:
            try:
                time.sleep(timeout_seconds)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"\n[timeout] process terminated after {timeout_seconds}s\n")
                        handle.flush()
            except Exception:
                pass

        threading.Thread(target=_watch_timeout, daemon=True).start()

    console.print(Panel.fit(f"Streaming background output to {log_path}", title="Background process"))
    stream_background_process_output(process, log_path)

    return {
        "status": "ok",
        "content": f"Started background process (pid={process.pid})",
        "pid": process.pid,
        "cwd": safe_cwd,
        "log_path": str(log_path),
        "state": "running",
    }


def get_background_process_status(process_id: int, log_path: str | os.PathLike[str] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    try:
        process = subprocess.Popen(["ps", "-p", str(process_id), "-o", "pid="], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = process.communicate(timeout=5)
        running = bool(stdout and str(stdout).strip())
    except Exception:
        running = False

    output_text = _read_text_file_tail(log_path) if log_path else ""
    payload = {
        "status": "running" if running else "completed",
        "pid": process_id,
        "log_path": str(log_path) if log_path else None,
        "output_tail": output_text,
    }
    if timeout_seconds is not None and timeout_seconds > 0 and not running:
        payload["timeout_seconds"] = timeout_seconds
    return payload


def wait_for_health_check(url: str, timeout_seconds: int = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if getattr(response, "status", 0) < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_subprocess_command(command: Any, cwd: str, shell: bool = False, timeout: int | None = None) -> tuple[int, str, str]:
    safe_cwd = resolve_execution_cwd(cwd, Path.cwd())
    try:
        proc = subprocess.Popen(
            command,
            cwd=safe_cwd,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("Failed to start subprocess in cwd=%s, falling back to %s: %s", cwd, safe_cwd, exc)
        proc = subprocess.Popen(
            command,
            cwd=str(Path.cwd()),
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return -1, "", f"Command timed out after {timeout} seconds"
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        raise
    return proc.returncode, stdout or "", stderr or ""


import os
import sys
import time
import uuid
import platform
import subprocess
import signal
from pathlib import Path

def _kill_process_tree(pid: int) -> None:
    """Cross-platform process tree termination."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5
            )
        else:
            # 杀死整个进程组，防止子进程成为孤儿进程
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(1)
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        pass

def handle_execute_python_script(args: dict) -> dict:
    script = args.get("script")
    if not script:
        return {"status": "error", "content": "Missing 'script' argument."}

    cwd = resolve_execution_cwd(args.get("cwd"), Path.cwd())
    timeout = int(args.get("timeout_seconds") or TOOL_SUBPROCESS_TIMEOUT)
    
    # 使用随机文件名防止并发冲突
    script_path = Path(cwd) / f"__cli_temp_script_{uuid.uuid4().hex[:8]}__.py"
    
    popen_kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,  # 屏蔽 stdin，防止 input() 导致永久阻塞
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }

    # 进程组隔离：防止子进程逃逸
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = None
    try:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        
        process = subprocess.Popen([sys.executable, str(script_path)], **popen_kwargs)
        
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            returncode = process.returncode
            
            # 截断过大的输出防止内存/Tokens爆炸
            max_chars = 8000
            if len(stdout) > max_chars:
                stdout = stdout[-max_chars:] + "\n[...stdout truncated...]"
            if len(stderr) > max_chars:
                stderr = stderr[-max_chars:] + "\n[...stderr truncated...]"

            content = stdout
            if stderr and stderr.strip():
                content += f"\n[STDERR]\n{stderr}"
                
            return {
                "status": "ok" if returncode == 0 else "error",
                "content": content if content.strip() else f"Script executed with exit code {returncode}.",
                "returncode": returncode
            }
            
        except subprocess.TimeoutExpired:
            # 超时后强杀整棵进程树
            if process:
                _kill_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            
            return {
                "status": "timeout",
                "content": f"Python script timed out after {timeout} seconds and was forcefully terminated.",
                "returncode": -1
            }

    except Exception as exc:
        return {"status": "error", "content": f"Failed to execute python script: {exc}"}
    finally:
        # 无论成功失败，清理临时文件
        try:
            if script_path.exists():
                script_path.unlink()
        except Exception:
            pass



def execute_tool_call(tool_call: Any) -> dict[str, Any]:
    name = getattr(tool_call.function, "name", "")
    args = safe_parse_tool_args(getattr(tool_call.function, "arguments", {}))
    ensure_not_interrupted()
    logger.info("Starting tool execution: {name}, args: {args}".format(name=name, args=args))

    if name == "read_file":
        file_path = args.get("path") or args.get("file_path")
        if not file_path:
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="read_file", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="write_file", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="delete_file", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="create_directory", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="delete_directory", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="rename_path", arg="old_path/new_path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="copy_file", arg="source_path/destination_path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="read_image_as_base64", arg="path")}
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
            result = {"status": "error", "content": "{name} is missing the {arg} argument.".format(name="list_dir", arg="path")}
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

    if name == "execute_python_script":
        return handle_execute_python_script(args)

    if name in {item["function"]["name"] for item in ACTIVE_MCP_TOOL_DEFINITIONS}:
        logger.info("Executing MCP tool: %s, args: %s", name, args)
        return run_mcp_tool(name, args)

    result = {"status": "error", "content": "Unknown tool: {name}".format(name=name)}
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
            title="Tool execution result",
        )
    )


def chat_once(client: OpenAI, model: str, messages: list[dict[str, Any]], temperature: float, debug_enabled: bool = False) -> Any:
    ensure_not_interrupted()
    wait_for_model_call_interval()
    tool_definitions = TOOL_DEFINITIONS + ACTIVE_MCP_TOOL_DEFINITIONS
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
    show_stage("Starting model call", f"model={model}\nmessages={len(messages)}\n{"Submitted context size"}={context_size_chars} chars / {context_size_bytes} bytes")
    if debug_enabled:
        show_stage("Debug - model request parameters", json.dumps(request_payload, ensure_ascii=False, indent=2))

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
            "Model call finished",
            f"model={model}\nresponse_type={type(assistant_message).__name__}\n{"Response content length"}={response_length}\n{"Cumulative tokens"}={usage_summary}",
        )
        if debug_enabled:
            raw_response = response.model_dump_json(indent=2)
            show_stage("Debug - raw model response", raw_response)

        return assistant_message
    except KeyboardInterrupt:
        logger.info("Model call interrupted by user")
        show_stage("Cancelled", "The current model call was interrupted by the user.")
        raise
    except Exception as exc:
        logger.exception("Error while calling the model")
        err_text = str(exc)
        exc_name = type(exc).__name__
        status_code = getattr(exc, "status_code", None)

        if "429" in err_text or "RateLimit" in exc_name or "free quota" in err_text or "RESOURCES_TIPS" in err_text:
            user_msg = "Model call failed: quota or rate-limit issue detected (the free quota may be exhausted or billing may not be enabled).
Please check the api_key and base_url in the config, or enable/top up the service and try again."
        elif status_code == 502 or "502" in err_text or "InternalServerError" in exc_name or "Bad Gateway" in err_text:
            user_msg = "Model call failed: the upstream model service returned 502 (Bad Gateway) or a server-side error.
This is usually caused by a temporary outage, proxy forwarding issue, or service restart.
Please try again later and confirm that base_url points to a service that serves /chat/completions correctly."
        else:
            user_msg = "Model call failed: {error}".format(error=f"{exc_name}: {err_text}")

        assistant_message = SimpleNamespace()
        assistant_message.tool_calls = []
        assistant_message.content = user_msg
        if debug_enabled:
            show_stage("Debug - model call error", traceback.format_exc())
        else:
            show_stage("Model call failed: {error}".format(error=user_msg), user_msg)
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
            self.md_path.write_text("# Recent conversations" + "\n\n", encoding="utf-8")

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

    planning_messages = [
        {"role": "system", "content": PLANNING_PROMPT},
        {"role": "user", "content": user_text},
    ]
    if history:
        planning_messages.insert(1, {"role": "user", "content": "以下是历史上下文：\n" + json.dumps(history, ensure_ascii=False)})

    assistant_message = chat_once(client, model, planning_messages, temperature=0.2, debug_enabled=debug_enabled)
    return (assistant_message.content or "").strip() or user_text


def is_special_command(user_text: str) -> bool:
    normalized = (user_text or "").strip()
    if not normalized:
        return False
    special_commands = {"/exit", "/clear", "/task-start", "/task-end"}
    return normalized in special_commands


def run_agent(client: OpenAI, model: str, system_prompt: str, session_store: SessionStore, user_text: str, debug_enabled: bool = False, recorder: ConversationRecorder | None = None) -> None:
    reset_interruption_state()
    history = session_store.load()
    planned_user_text = user_text
    if not is_special_command(user_text):
        try:
            planned_user_text = plan_user_request(client, model, history, user_text, debug_enabled=debug_enabled)
        except Exception:
            logger.exception("Planning step failed; continuing with original user input")

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
            console.print(Panel.fit("The current task has been cancelled.", title="Interrupted"))
            if recorder:
                recorder.record_error("User interrupted the current task")
            return
        except Exception:
            logger.exception("Chat request failed")
            console.print(Panel.fit(traceback.format_exc(), title="Runtime error"))
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
        # if recorder:
        #     recorder.record_assistant(assistant_message.content or "开始调用方法function calling...")

        for tc in tool_calls:
            try:
                tool_desc = get_tool_description(tc)
                start_calling_tips = f"tool={tc.function.name}\ndescription={tool_desc}\narguments={tc.function.arguments}"
                show_stage("Starting tool call", start_calling_tips[:80])
                # if recorder:
                #     recorder.record_tool_start(tc.function.name, tc.function.arguments)
                result = execute_tool_call(tc)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
                show_tool_result(tc, result)
                # end_calling_tips = f"tool={tc.function.name}\nresult={json.dumps(result, ensure_ascii=False)}"
                # show_stage("Tool call finished", end_calling_tips[:80])

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
                # if recorder:
                #     recorder.record_tool_result(tc.function.name, result)
            except KeyboardInterrupt:
                logger.info("Tool execution interrupted by user, tool=%s", tc.function.name)
                console.print(Panel.fit("The current tool execution has been cancelled.", title="Interrupted"))
                # if recorder:
                #     recorder.record_error(f"用户中断工具执行: {tc.function.name}")
                return
            except Exception:
                logger.exception("Tool execution error, tool=%s", tc.function.name)
                error_payload = {"status": "error", "content": traceback.format_exc()}
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(error_payload, ensure_ascii=False)})
                console.print(Panel.fit(traceback.format_exc(), title="Tool execution error"))
                # if recorder:
                #     recorder.record_error(traceback.format_exc())


def handle_task_end_command(md_path: Path, client: OpenAI, model: str, system_prompt: str, workdir: Path, debug_enabled: bool = False) -> None:
    """Process recent_conversations.md to find the most recent /task-start and use the
    subsequent rounds to ask the model to produce an improved prompt, then write last-prompt.md.
    """
    if not md_path.exists():
        console.print(Panel.fit("Conversation history file not found: {path}".format(path=md_path), title="Task End error"))
        return

    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("\n## Round ")
    if len(parts) <= 1:
        console.print(Panel.fit("Conversation history is empty or malformed.", title="Task End error"))
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
        console.print(Panel.fit("No round containing /task-start was found in recent_conversations.md.", title="Task End notice"))
        return

    selected = rounds[start_index:]
    compiled_text = "\n\n".join(["## Round " + r for r in selected])

    # Build messages for the model
    sys_prompt = (
        "You are a prompt-optimization expert skilled at turning conversation history into a clear and actionable task prompt.
Input: first the user provides an initial prompt after /task-start, then the conversation history from that point onward including model replies, tool calls, and results.
Task: read these records, understand the initial prompt and any follow-up clarifications or changes, and generate an improved final prompt that is clear, executable, and ready to use.
Output requirements: return only the final improved prompt text, with no explanation, metadata, or comments. Keep it within 18000 characters."
    )

    user_msg = (
        "Conversation history (starting from /task-start, from oldest to newest):

{compiled_text}".format(compiled_text=compiled_text)
    )

    try:
        assistant_message = chat_once(client, model, [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}], temperature=0.2, debug_enabled=debug_enabled)
    except Exception:
        logger.exception("Failed to generate the final prompt with the model")
        console.print(Panel.fit(traceback.format_exc(), title="Generation failed"))
        return

    final_prompt = (assistant_message.content or "").strip()
    if not final_prompt:
        console.print(Panel.fit("The model did not return a final prompt.", title="Generation result"))
        return

    out_path = workdir / "last-prompt.md"
    out_text = "# Final prompt" + "\n\n" + final_prompt + "\n"
    out_path.write_text(out_text, encoding="utf-8")
    console.print(Panel.fit("Final prompt generated and written to: {path}".format(path=out_path), title="Task End complete"))


def interactive_loop(client: OpenAI, model: str, system_prompt: str, session_store: SessionStore, history_file: Path, debug_enabled: bool = False, recorder: ConversationRecorder | None = None) -> None:
    console.print(Panel.fit(
        "[bold green]Prompt Pilot CLI Coding Agent[/bold green]\n"
        + "Input:
/exit to exit,
/clear to clear the local session,
/task-start to start a task context,
/task-end to generate the final prompt.
Ctrl+C can interrupt the current execution.

Startup command examples:
python main.py -t \"your task\" -d ./workspace
python main.py --reset-session

Options:
-t / --task: one-off task content
-d / --workdir: specify the working directory
-amc / --agent-messages-count: number of messages to keep in agent history (default: 6)
-rd / --request-delay: delay in seconds between model requests (default: 8)
-hc / --history-count: number of rounds to keep in conversation history (default: 5)
--reset-session: reset the local session history." + "\n",
        title="Start",
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
            user_text = session.prompt("What do you want me to do? > ")
        except KeyboardInterrupt:
            console.print("\n" + "Exited.")
            return

        if user_text.strip() == "/exit":
            console.print("Goodbye.")
            return
        if user_text.strip() == "/clear":
            session_store.save([])
            recent_conversations_path = ROOT / "recent_conversations.md"
            if recent_conversations_path.exists():
                recent_conversations_path.write_text("# Recent conversations" + "\n\n", encoding="utf-8")
            console.print("Session history and recent conversation history have been cleared.")
            continue
        if user_text.strip() == "/task-end":
            # Process recent_conversations.md and generate last-prompt.md
            md_path = recorder.md_path if recorder else (ROOT / "recent_conversations.md")
            handle_task_end_command(md_path, client, model, system_prompt, WORKSPACE_DIR, debug_enabled=debug_enabled)
            continue

        run_agent(client, model, system_prompt, session_store, user_text, debug_enabled=debug_enabled, recorder=recorder)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prompt Copilot CLI coding agent")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {APPLICATION_VERSION}")
    parser.add_argument("-t", "--task", help="One-off task content, suitable for a single run.")
    parser.add_argument("-d", "--workdir", default=WORKSPACE_DIR, help="Working directory.")
    parser.add_argument("-amc", "--agent-messages-count", default=CHAT_MESSAGE_MAX_COUNT, help="number of messages to keep in agent history (default: 6)")
    parser.add_argument("-rd", "--request-delay", default=RE_ACTION_DELAY, help="delay in seconds between model requests (default: 8)")
    parser.add_argument("-hc", "--history-count", default=DEFAULT_MAX_CHAT_COUNT, help="number of rounds to keep in conversation history (default: 5)")
    parser.add_argument("--reset-session", action="store_true", help="Reset local persisted session history.")
    return parser


def main() -> None:
    global DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, DEFAULT_MAX_CHAT_COUNT, CHAT_MESSAGE_MAX_COUNT

    parser = build_cli_parser()
    args = parser.parse_args()

    RE_ACTION_DELAY = int(args.request_delay)
    DEFAULT_MAX_CHAT_COUNT = int(args.history_count)
    CHAT_MESSAGE_MAX_COUNT = int(args.agent_messages_count)

    DEFAULT_SYSTEM_PROMPT = ZH_SYSTEM_PROMPT

    # set up workspace directory
    WORKSPACE_DIR = Path(args.workdir)
    console.print(Panel.fit("Working directory: {path}".format(path=WORKSPACE_DIR), title="CLI configuration"))

    try:
        model_cfg, system_prompt = ensure_config(workdir=WORKSPACE_DIR)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title="Configuration error"))
        return

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WORKSPACE_DIR)

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)

    mcp_tools = discover_mcp_tools(model_cfg)
    if mcp_tools:
        console.print(Panel.fit("Connected MCP tools: {count}".format(count=len(mcp_tools)), title="MCP configuration"))

    try:
        client = build_client(model_cfg)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title="Configuration error"))
        return

    session_file = ROOT / ".session_history.json"
    history_file = ROOT / ".history"

    session_store = SessionStore(session_file, max_messages=DEFAULT_MAX_CHAT_COUNT)
    if args.reset_session:
        session_store.save([])
        recent_conversations_path = ROOT / "recent_conversations.md"
        if recent_conversations_path.exists():
            recent_conversations_path.write_text("# Recent conversations" + "\n\n", encoding="utf-8")
        console.print("Session history has been reset.")

    debug_enabled = bool(model_cfg.get("debug", False))
    if debug_enabled:
        console.print(Panel.fit("Debug mode is enabled; model request parameters and raw responses will be shown.", title="Debug configuration"))

    # Create conversation recorder to persist recent rounds to markdown
    recorder = ConversationRecorder(ROOT / "recent_conversations.md", max_rounds=188)

    if args.task:
        try:
            run_agent(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, args.task, debug_enabled=debug_enabled, recorder=recorder)
        except KeyboardInterrupt:
            console.print(Panel.fit("The current task has been cancelled.", title="Interrupted"))
        return

    try:
        interactive_loop(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, history_file, debug_enabled=debug_enabled, recorder=recorder)
    except KeyboardInterrupt:
        console.print(Panel.fit("The current task has been cancelled.", title="Interrupted"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        console.print(Panel.fit(traceback.format_exc(), title="Runtime error"))
        sys.exit(1)
