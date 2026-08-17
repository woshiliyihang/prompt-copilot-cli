"""Production-grade long-term memory backed by SQLite + FTS5.

Design goals:

* **Durability** — memories live in ``~/.prompt-copilot/memory.db`` and
  survive across sessions.
* **Hybrid retrieval** — FTS5 full-text ranking (bm25) with CJK-friendly
  per-character segmentation, plus a ``LIKE`` fallback for queries the
  tokenizer cannot match.
* **Safety** — entries are validated (non-empty, length-capped) and obvious
  secrets (API keys, passwords, private keys, bearer tokens) are rejected.
* **Zero extra dependencies** — sqlite3 ships with CPython; FTS5 is enabled
  in every officially distributed build, and the store degrades to LIKE-only
  search when it is not.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .globals_ import MEMORY_DB_PATH, logger

MAX_MEMORY_CHARS = 300
VALID_KINDS = {"fact", "preference", "decision", "lesson"}

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+"),
]

_CJK_RE = re.compile(r"([\u3400-\u9fff\uf900-\ufaff])")


def _segment(text: str) -> str:
    """Insert spaces around CJK characters so each becomes an FTS5 token."""
    return _CJK_RE.sub(r" \1 ", text)


def _build_match_query(query: str) -> str:
    tokens = _segment(query).split()
    if not tokens:
        return ""
    return " OR ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)


def looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


class MemoryStore:
    """SQLite-backed memory store with FTS5 hybrid search."""

    def __init__(self, db_path: str | Path = MEMORY_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self.fts_enabled = self._ensure_schema()

    def _ensure_schema(self) -> bool:
        conn = self._conn
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'fact',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content_seg)")
            conn.commit()
            return True
        except sqlite3.OperationalError:
            logger.warning("FTS5 unavailable; long-term memory falls back to LIKE search")
            conn.commit()
            return False

    # ------------------------------------------------------------------ write

    def add(self, content: str, kind: str = "fact") -> dict[str, Any]:
        text = (content or "").strip()
        if not text:
            return {"status": "error", "code": "empty", "content": "empty memory"}
        if len(text) > MAX_MEMORY_CHARS:
            return {"status": "error", "code": "too_long", "content": f"memory exceeds {MAX_MEMORY_CHARS} chars"}
        if looks_like_secret(text):
            return {"status": "error", "code": "secret", "content": "memory appears to contain secrets"}

        kind = kind if kind in VALID_KINDS else "fact"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._conn
        existing = conn.execute(
            "SELECT id FROM memories WHERE content = ?", (text,)
        ).fetchone()
        if existing is not None:
            return {"status": "ok", "id": existing["id"], "duplicate": True}

        cur = conn.execute(
            "INSERT INTO memories(content, kind, created_at) VALUES (?, ?, ?)",
            (text, kind, now),
        )
        memory_id = int(cur.lastrowid)
        if self.fts_enabled:
            conn.execute(
                "INSERT INTO memories_fts(rowid, content_seg) VALUES (?, ?)",
                (memory_id, _segment(text)),
            )
        conn.commit()
        return {"status": "ok", "id": memory_id, "duplicate": False}

    def delete(self, memory_id: int) -> bool:
        conn = self._conn
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
        if self.fts_enabled:
            conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (int(memory_id),))
        conn.commit()
        return True

    # ------------------------------------------------------------------- read

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, content, kind, created_at FROM memories ORDER BY id DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
        return int(row["n"]) if row else 0

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        limit = max(0, int(limit))
        if limit == 0 or not (query or "").strip():
            return []

        results: list[dict[str, Any]] = []
        if self.fts_enabled:
            match_query = _build_match_query(query)
            if match_query:
                try:
                    rows = self._conn.execute(
                        """
                        SELECT m.id, m.content, m.kind, m.created_at, bm25(memories_fts) AS rank
                        FROM memories_fts AS f
                        JOIN memories AS m ON m.id = f.rowid
                        WHERE memories_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (match_query, limit),
                    ).fetchall()
                    results = [dict(row) for row in rows]
                except sqlite3.OperationalError:
                    results = []

        if not results:
            # LIKE fallback keeps recall for queries the tokenizer cannot match.
            like = f"%{query.strip()}%"
            rows = self._conn.execute(
                """
                SELECT id, content, kind, created_at
                FROM memories WHERE content LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (like, limit),
            ).fetchall()
            results = [dict(row) for row in rows]

        return results

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


_default_store: MemoryStore | None = None


def default_store() -> MemoryStore:
    """Return the process-wide store rooted at ``MEMORY_DB_PATH``."""
    global _default_store
    if _default_store is None:
        _default_store = MemoryStore(MEMORY_DB_PATH)
    return _default_store


def reset_default_store() -> None:
    global _default_store
    if _default_store is not None:
        _default_store.close()
    _default_store = None


def format_memories_for_prompt(memories: list[dict[str, Any]]) -> str:
    """Render retrieved memories as a compact block for prompt injection."""
    from .i18n import t

    if not memories:
        return ""
    lines = [t("memory_section_header")]
    for item in memories:
        lines.append(f"- [{item.get('kind', 'fact')}] {item.get('content', '')}")
    return "\n".join(lines)


# ----------------------------------------------------------- auto extraction

EXTRACTION_SYSTEM_PROMPT_ZH = """\
你是记忆提炼助手。请从下面的任务对话记录中提取值得跨会话长期保存的信息，只提取：
- 用户明确的偏好或约定（preference）
- 项目/环境的关键事实（fact）
- 已确认的重要决策（decision）
- 可复用的经验教训（lesson）

要求：
1. 每条记忆必须简洁、自包含、可复用，不超过 100 字。
2. 严禁包含密钥、密码、token 等敏感信息。
3. 只输出一个 JSON 数组，元素为字符串；没有可提取内容时输出 []。
4. 最多输出 5 条，宁缺毋滥。
"""

EXTRACTION_SYSTEM_PROMPT_EN = """\
You are a memory-distillation assistant. From the task conversation below, extract information worth keeping across sessions:
- explicit user preferences or conventions (preference)
- key facts about the project or environment (fact)
- confirmed important decisions (decision)
- reusable lessons learned (lesson)

Rules:
1. Each memory must be concise, self-contained and reusable, at most 100 characters.
2. Never include secrets such as API keys, passwords or tokens.
3. Output a single JSON array of strings only; output [] when nothing qualifies.
4. At most 5 entries; prefer fewer over noise.
"""


def parse_extracted_memories(raw_text: str) -> list[str]:
    """Parse the model reply into a list of memory strings, tolerating fences."""
    text = (raw_text or "").strip()
    if not text:
        return []
    # Strip common code fences.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    # Locate the first JSON array.
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    cleaned: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip()[:MAX_MEMORY_CHARS])
    return cleaned[:5]


def extract_memories_via_model(client: Any, model: str, transcript: str, language: str) -> list[str]:
    """Ask the model to distill long-term memories from *transcript*.

    Import of ``chat_once`` is deferred to avoid circular imports.
    """
    from .llm import chat_once

    system_prompt = (
        EXTRACTION_SYSTEM_PROMPT_ZH if str(language).lower().startswith("zh") else EXTRACTION_SYSTEM_PROMPT_EN
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript[-12000:]},
    ]
    assistant_message = chat_once(client, model, messages, temperature=0.1, disable_tools=True)
    return parse_extracted_memories(getattr(assistant_message, "content", "") or "")


def auto_extract_memories(
    client: Any,
    model: str,
    transcript: str,
    language: str,
    store: MemoryStore | None = None,
) -> int:
    """Extract and persist memories; returns the number of newly stored entries."""
    target = store or default_store()
    if not (transcript or "").strip():
        return 0
    try:
        candidates = extract_memories_via_model(client, model, transcript, language)
    except Exception:
        logger.exception("Memory auto-extraction model call failed")
        return 0

    stored = 0
    for item in candidates:
        outcome = target.add(item, kind="fact")
        if outcome.get("status") == "ok" and not outcome.get("duplicate"):
            stored += 1
    if stored:
        logger.info("Auto-extracted %d long-term memories", stored)
    return stored
