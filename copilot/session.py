"""Session persistence: the sliding-window history stored on disk."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .globals_ import settings


class SessionStore:
    """Persists conversation history as a JSON array capped at ``max_messages``."""

    def __init__(self, session_file: Path, max_messages: int | None = None):
        self.session_file = session_file
        self.max_messages = max_messages if max_messages is not None else settings.session_max_messages
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
            history = history[-self.max_messages :]
        self.session_file.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
