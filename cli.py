from __future__ import annotations
from config import APPLICATION_VERSION
from config import CHAT_MESSAGE_MAX_COUNT
from config import DEFAULT_MAX_CHAT_COUNT
from config import MEMORY_FILE_PATH
from config import RE_ACTION_DELAY
from config import ROOT
from config import WORKSPACE_DIR
from config import t
from mcp import console
from model import chat_once
from model import format_cumulative_token_summary
from openai import OpenAI
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.panel import Panel
from session import ConversationRecorder
from session import SessionStore
from session import append_task_memory_entry
from session import clear_task_memory_file
from session import summarize_memory_value
from session import summarize_tool_result
from tools import build_multimodal_user_message
from tools import execute_tool_call
from tools import get_content_from_tool_calls
from tools import get_tool_description
from tools import logger
from tools import parse_tool_calls_from_content
from tools import sanitize_tool_result_for_display
from tools import to_tool_call_objects
from typing import Any
import argparse
import json
import traceback

def build_bottom_toolbar_text() -> str:
    base = t("toolbar_help")
    token_summary = format_cumulative_token_summary()
    return f"{token_summary} | {base}"

def reset_interruption_state() -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = False

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
