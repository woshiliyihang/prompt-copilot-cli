from __future__ import annotations
from config import APPLICATION_VERSION
from config import CHAT_MESSAGE_MAX_COUNT
from config import DEFAULT_MAX_CHAT_COUNT
from config import RE_ACTION_DELAY
from config import ROOT
from config import WORKSPACE_DIR
from config import console
from config import t
from model import format_cumulative_token_summary
from openai import OpenAI
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.panel import Panel
from session import ConversationRecorder
from session import SessionStore
from tools import get_tool_description
from tools import sanitize_tool_result_for_display
from typing import Any
import argparse
import json

def build_bottom_toolbar_text() -> str:
    base = t("toolbar_help")
    token_summary = format_cumulative_token_summary()
    return f"{token_summary} | {base}"

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
