from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_openai import ChatOpenAI

from .config import MEMORY_DB_PATH
from .memory import MemoryRuntime
from .tools import Workspace, build_tools

SYSTEM_PROMPT = """You are a pragmatic terminal coding agent.

Your job is to inspect, modify, and test software in the supplied workspace.

Rules:
- Inspect relevant files before editing. Do not invent project structure.
- Prefer search_code and read_file before making changes.
- Use edit_file for targeted changes; use write_file when replacing/creating a whole file.
- After code changes, run the smallest relevant test, lint, or build command.
- Treat tool output as evidence. If a command fails, diagnose and fix it instead of claiming success.
- Keep changes focused on the user's request. Do not refactor unrelated code.
- You have persistent long-term memory tools. Store durable project decisions and user preferences, not transient command output.
- You can use memory search when previous project decisions or conventions are relevant.
- Never claim a change was made unless a tool result confirms it.
"""


class AgentRuntime:
    """Small composition root: model + tools + LangGraph persistence."""

    def __init__(self, workspace: str | Path, config: dict[str, Any]):
        self.workspace = Workspace(workspace)
        self.config = config
        self.workspace_id = hashlib.sha256(str(self.workspace.root).encode("utf-8")).hexdigest()[:24]
        self.thread_id = self._load_thread_id()
        self.memory = MemoryRuntime(config.get("memory_db_path", MEMORY_DB_PATH))
        self.model = ChatOpenAI(
            model=str(config["model"]),
            api_key=str(config["api_key"]),
            base_url=str(config["base_url"]),
            temperature=float(config.get("temperature", 0.2)),
            timeout=float(config.get("timeout", 120)),
            max_retries=2,
        )
        self.agent = self._build_agent()

    def _load_thread_id(self) -> str:
        path = self.workspace.root / ".prompt-copilot-thread"
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        path.write_text(value, encoding="utf-8")
        return value

    def _build_agent(self):
        tools = build_tools(self.workspace)
        if self.config.get("memory", {}).get("enabled", True):
            tools.extend(self.memory.tools())

        context_cfg = self.config.get("context", {})
        middleware = [
            SummarizationMiddleware(
                model=self.model,
                trigger=("tokens", int(context_cfg.get("summary_trigger_tokens", 12000))),
                keep=("messages", int(context_cfg.get("keep_messages", 20))),
            )
        ]
        return create_agent(
            self.model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware,
            checkpointer=self.memory.checkpointer,
            store=self.memory.store,
            name="prompt_copilot",
        )

    def _memory_context(self, user_text: str) -> str:
        if not self.config.get("memory", {}).get("enabled", True):
            return ""
        namespace = ("memories", self.workspace_id)
        limit = int(self.config.get("memory", {}).get("max_recent_memories", 5))
        try:
            items = self.memory.store.search(namespace, query=user_text, limit=max(1, limit))
        except Exception:
            items = self.memory.store.search(namespace, limit=max(1, limit))
        if not items:
            return ""
        lines = ["Relevant durable project memory:"]
        for item in items:
            value = item.value
            lines.append(f"- {value.get('content', value) if isinstance(value, dict) else value}")
        return "\n".join(lines)

    def invoke(self, user_text: str) -> str:
        memory_context = self._memory_context(user_text)
        content = user_text if not memory_context else f"{memory_context}\n\nCurrent request:\n{user_text}"
        config = {
            "configurable": {
                "thread_id": self.thread_id,
                "workspace_id": self.workspace_id,
            }
        }
        result = self.agent.invoke({"messages": [{"role": "user", "content": content}]}, config)
        messages = result.get("messages", [])
        if not messages:
            return ""
        final = messages[-1]
        value = getattr(final, "content", final.get("content", "") if isinstance(final, dict) else "")
        if isinstance(value, list):
            return "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in value)
        return str(value or "")

    def reset(self) -> None:
        self.memory.checkpointer.delete_thread(self.thread_id)
        self.thread_id = str(uuid.uuid4())
        (self.workspace.root / ".prompt-copilot-thread").write_text(self.thread_id, encoding="utf-8")

    def close(self) -> None:
        self.memory.close()
