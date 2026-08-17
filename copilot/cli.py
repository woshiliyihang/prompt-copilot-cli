from __future__ import annotations

import argparse
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel

from .agent import AgentRuntime
from .config import CONFIG_PATH, load_config
from .memory import MemoryRuntime

console = Console()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Simple LangChain coding agent")
    p.add_argument("-t", "--task", help="Run one task and exit")
    p.add_argument("-d", "--workdir", default=".", help="Agent workspace")
    p.add_argument("--reset-session", action="store_true", help="Reset the current workspace conversation")
    p.add_argument("--version", action="version", version="prompt-copilot 0.5.9")
    return p


def _key_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-j")
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    return kb


def _show_memory(runtime: AgentRuntime) -> None:
    rows = runtime.memory.store.search(("memories", runtime.workspace_id), limit=20)
    if not rows:
        console.print("No long-term memories.")
        return
    for item in rows:
        value = item.value
        text = value.get("content", value) if isinstance(value, dict) else value
        console.print(f"• {text}")


class SlashCommandCompleter(Completer):
    COMMANDS = [
        "/exit",
        "/clear",
        "/memory",
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        prefix = text.strip()
        for command in self.COMMANDS:
            if command.startswith(prefix):
                yield Completion(command, start_position=-len(prefix))


def run_interactive(runtime: AgentRuntime) -> None:
    history_path = runtime.workspace.root / ".prompt-copilot-history"
    session = PromptSession(
        history=FileHistory(str(history_path)),
        multiline=True,
        key_bindings=_key_bindings(),
        completer=SlashCommandCompleter(),
        bottom_toolbar="Enter: send   Ctrl+J: newline   /exit: quit   /clear: reset conversation   /memory: show memory",
    )
    console.print(Panel.fit(
        f"[bold]Prompt Copilot[/bold]\nWorkspace: {runtime.workspace.root}\nModel: {runtime.config['model']}\nConfig: {CONFIG_PATH}",
        title="LangChain Coding Agent",
    ))

    while True:
        try:
            text = session.prompt("You › ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.")
            return
        if not text:
            continue
        if text == "/exit":
            return
        if text == "/clear":
            runtime.reset()
            console.print("Conversation reset.")
            continue
        if text == "/memory":
            _show_memory(runtime)
            continue
        _run_task(runtime, text)


def _run_task(runtime: AgentRuntime, text: str) -> None:
    if runtime.config.get("debug", False):
        console.print(Panel(
            f"workspace={runtime.workspace.root}\nthread={runtime.thread_id}\nrequest={text[:300]}",
            title="Debug",
            border_style="yellow",
        ))
    try:
        answer = runtime.invoke(text)
        if answer:
            console.print(Panel(answer, title="Agent"))
    except KeyboardInterrupt:
        console.print("Task interrupted.")
    except Exception as exc:
        console.print(Panel(str(exc), title="Agent error", border_style="red"))


def main() -> None:
    args = parser().parse_args()
    try:
        config = load_config()
        runtime = AgentRuntime(Path(args.workdir), config)
        try:
            if args.reset_session:
                runtime.reset()
            if args.task:
                _run_task(runtime, args.task)
            else:
                run_interactive(runtime)
        finally:
            runtime.close()
    except Exception as exc:
        console.print(Panel(str(exc), title="Startup error", border_style="red"))
