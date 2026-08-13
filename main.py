from __future__ import annotations

from cli import show_stage
from cli import show_tool_result
from config import CHAT_MESSAGE_MAX_COUNT
from config import DEFAULT_MAX_CHAT_COUNT
from config import LOG_DIR
from config import LOG_FILE
from config import MEMORY_FILE_PATH
from config import ROOT
from config import WORKSPACE_DIR
from config import build_client
from config import console
from config import ensure_config
from config import logger
from config import t
from mcp import discover_mcp_tools
from model import chat_once
from session import ConversationRecorder
from session import SessionStore
from session import append_task_memory_entry
from session import clear_task_memory_file
from session import summarize_memory_value
from session import summarize_tool_result
from tools import build_multimodal_user_message
from tools import execute_tool_call
from tools import get_content_from_tool_calls
from tools import parse_tool_calls_from_content
from tools import to_tool_call_objects

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


ROOT.mkdir(parents=True, exist_ok=True)
































logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
INTERRUPTION_REQUESTED = False


def handle_sigint(signum: int, frame: Any) -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = True
    raise KeyboardInterrupt(t("interrupt"))




def reset_interruption_state() -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = False






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













































import os
import signal
import subprocess
import uuid
import time
import platform
from pathlib import Path




















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

import json
from typing import List, Union




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
