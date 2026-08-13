from __future__ import annotations
from config import DEFAULT_MAX_CHAT_COUNT
from config import MEMORY_FILE_PATH
from config import t
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

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
