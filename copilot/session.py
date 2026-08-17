"""Session persistence: a small, resilient sliding-window history store."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .globals_ import settings


class SessionStore:
    """Persist a bounded conversation history as JSON.

    The store intentionally stays lightweight: history is still just a JSON
    list, but reads are validated and writes are atomic so a process
    interruption cannot leave a half-written session file.
    """

    def __init__(self, session_file: Path, max_messages: int | None = None):
        self.session_file = Path(session_file)
        configured_limit = max_messages if max_messages is not None else settings.session_max_messages
        try:
            self.max_messages = max(1, int(configured_limit))
        except (TypeError, ValueError):
            self.max_messages = 1
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.session_file.exists():
            self._atomic_write([])

    def load(self) -> list[dict[str, Any]]:
        """Load only well-formed message dictionaries; recover from bad JSON."""
        try:
            payload = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return []

        if not isinstance(payload, list):
            return []

        messages: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("role"), str):
                messages.append(item)
        return messages[-self.max_messages :]

    def _atomic_write(self, history: list[dict[str, Any]]) -> None:
        """Replace the session file atomically in the same directory."""
        payload = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.session_file.name}.",
            suffix=".tmp",
            dir=str(self.session_file.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.session_file)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def save(self, history: list[dict[str, Any]]) -> None:
        """Persist a bounded copy without mutating the caller's list."""
        valid = [
            item for item in history
            if isinstance(item, dict) and isinstance(item.get("role"), str)
        ]
        self._atomic_write(valid[-self.max_messages :])
