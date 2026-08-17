from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore
from langmem import create_manage_memory_tool, create_search_memory_tool

from .config import MEMORY_DB_PATH


class MemoryRuntime:
    """Persistent LangGraph checkpoint + long-term memory store.

    SQLite keeps the CLI self-contained. The storage abstraction is LangGraph's
    BaseStore, so the backend can later be switched to Postgres without changing
    the agent or memory tools.
    """

    def __init__(self, path: str | Path = MEMORY_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._store_conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._checkpoint_conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.store = SqliteStore(self._store_conn)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)
        self.store.setup()
        self.checkpointer.setup()

    def tools(self):
        return [
            create_search_memory_tool(
                namespace=("memories", "{workspace_id}"),
                store=self.store,
                name="search_memory",
            ),
            create_manage_memory_tool(
                namespace=("memories", "{workspace_id}"),
                store=self.store,
                name="manage_memory",
            ),
        ]

    def recent(self, workspace_id: str, limit: int = 5) -> list[str]:
        items = self.store.search(("memories", workspace_id), limit=max(0, limit))
        return [str(item.value.get("content", item.value)) for item in items]

    def close(self) -> None:
        self._store_conn.close()
        self._checkpoint_conn.close()
