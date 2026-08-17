"""CLI entry point: argument parsing, startup wiring and the interactive loop."""
from __future__ import annotations

import argparse
import json
import os
import signal
import traceback
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from . import globals_, tools as tools_module
from .agent import ConversationRecorder, handle_task_end_command, run_agent
from .config import CONFIG_SAVE_FILE_PATH, ensure_config, build_client, memory_settings
from .globals_ import (
    APPLICATION_VERSION,
    HISTORY_FILE,
    RECENT_CONVERSATIONS_PATH,
    ROOT,
    SESSION_FILE,
    build_bottom_toolbar_text,
    handle_sigint,
    logger,
    settings,
)
from .i18n import set_language, t
from .memory import default_store, format_memories_for_prompt
from .mcp import discover_mcp_tools
from .session import SessionStore
from .ui import console


class SlashCommandCompleter(Completer):
    def __init__(self, commands: list[str]):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return
        if " " in text:
            return
        for command in self.commands:
            if command.startswith(text):
                yield Completion(command, start_position=-len(text))


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=t("cli_parser_description"))
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {APPLICATION_VERSION}")
    parser.add_argument("-t", "--task", help=t("task_argument_help"))
    parser.add_argument("-d", "--workdir", default=None, help=t("workdir_argument_help"))
    parser.add_argument("-l", "--lang", default="en", help="language code for localization (default: en)")
    parser.add_argument(
        "-amc",
        "--agent-messages-count",
        type=int,
        default=8,
        help="number of messages to keep per model request (default: 8)",
    )
    parser.add_argument(
        "-rd",
        "--request-delay",
        type=float,
        default=1,
        help="delay in seconds between model requests (default: 1)",
    )
    parser.add_argument(
        "-hc",
        "--history-count",
        type=int,
        default=6,
        help="number of messages to persist in session history (default: 6)",
    )
    parser.add_argument("--reset-session", action="store_true", help=t("reset_session_help"))
    return parser


def _handle_memory_command() -> None:
    """Show the most recent long-term memories."""
    store = default_store()
    rows = store.list_recent(limit=20)
    if not rows:
        console.print(Panel.fit(t("memory_empty"), title=t("memory_title")))
        return
    lines = []
    for row in rows:
        lines.append(f"#{row['id']} [{row['kind']}] {row['created_at']}  {row['content']}")
    console.print(Panel.fit("\n".join(lines), title=t("memory_title")))


def interactive_loop(
    client: Any,
    model: str,
    system_prompt: str,
    session_store: SessionStore,
    history_file: Path,
    debug_enabled: bool = False,
    recorder: ConversationRecorder | None = None,
    model_cfg: dict[str, Any] | None = None,
    language: str = "en",
) -> None:
    console.print(
        Panel.fit(
            "[bold green]Prompt Pilot CLI Coding Agent[/bold green]\n" + t("welcome_message") + "\n",
            title=t("startup_title"),
        )
    )

    slash_commands = ["/exit", "/clear", "/task-start", "/task-end", "/memory"]
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

        stripped = user_text.strip()
        if stripped == "/exit":
            console.print(t("bye_message"))
            return
        if stripped == "/clear":
            session_store.save([])
            if RECENT_CONVERSATIONS_PATH.exists():
                RECENT_CONVERSATIONS_PATH.write_text(
                    t("recent_conversations_header") + "\n\n", encoding="utf-8"
                )
            console.print(t("clear_session_message"))
            continue
        if stripped == "/task-end":
            md_path = recorder.md_path if recorder else RECENT_CONVERSATIONS_PATH
            handle_task_end_command(
                md_path,
                client,
                model,
                system_prompt,
                settings.workspace_dir,
                debug_enabled=debug_enabled,
                language=language,
            )
            continue
        if stripped == "/memory":
            _handle_memory_command()
            continue

        run_agent(
            client,
            model,
            system_prompt,
            session_store,
            user_text,
            debug_enabled=debug_enabled,
            recorder=recorder,
            model_cfg=model_cfg,
            language=language,
        )


def main() -> None:
    parser = build_cli_parser()
    args = parser.parse_args()

    settings.request_delay = args.request_delay
    settings.session_max_messages = args.history_count
    settings.agent_max_messages = args.agent_messages_count

    set_language(args.lang)

    settings.workspace_dir = Path(args.workdir) if args.workdir else ROOT / "workspace"
    console.print(Panel.fit(t("workdir_message", path=settings.workspace_dir), title=t("cli_config_title")))

    try:
        model_cfg, system_prompt = ensure_config(workdir=settings.workspace_dir, language=args.lang)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(settings.workspace_dir)

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)

    memory_cfg = memory_settings(model_cfg)
    tools_module.refresh_tool_definitions(memory_enabled=memory_cfg["enabled"])

    mcp_tools = discover_mcp_tools(model_cfg)
    if mcp_tools:
        console.print(Panel.fit(t("mcp_connected", count=len(mcp_tools)), title=t("mcp_config_title")))

    try:
        client = build_client(model_cfg)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    session_store = SessionStore(SESSION_FILE, max_messages=settings.session_max_messages)
    if args.reset_session:
        session_store.save([])
        if RECENT_CONVERSATIONS_PATH.exists():
            RECENT_CONVERSATIONS_PATH.write_text(
                t("recent_conversations_header") + "\n\n", encoding="utf-8"
            )
        console.print(t("session_reset_message"))

    debug_enabled = bool(model_cfg.get("debug", False))
    if debug_enabled:
        console.print(Panel.fit(t("debug_enabled_message"), title=t("debug_config_title")))

    recorder = ConversationRecorder(RECENT_CONVERSATIONS_PATH, max_rounds=settings.history_rounds)

    model = model_cfg.get("model", "gpt-4o-mini")

    if args.task:
        try:
            run_agent(
                client,
                model,
                system_prompt,
                session_store,
                args.task,
                debug_enabled=debug_enabled,
                recorder=recorder,
                model_cfg=model_cfg,
                language=args.lang,
            )
        except KeyboardInterrupt:
            console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
        return

    try:
        interactive_loop(
            client,
            model,
            system_prompt,
            session_store,
            HISTORY_FILE,
            debug_enabled=debug_enabled,
            recorder=recorder,
            model_cfg=model_cfg,
            language=args.lang,
        )
    except KeyboardInterrupt:
        console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
