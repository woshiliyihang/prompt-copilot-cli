"""Global paths, runtime settings, interruption state and token accounting.

Every mutable runtime value lives here as a module attribute so that all
other modules access it through attribute lookup (``globals_.settings.x``),
which keeps behaviour predictable and test-friendly.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .i18n import t

APPLICATION_VERSION = "0.3.0"

ROOT = Path.home() / ".prompt-copilot"
ROOT.mkdir(parents=True, exist_ok=True)

LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "agent_runtime.log"
MEMORY_DB_PATH = ROOT / "memory.db"
MEMORY_FILE_PATH = ROOT / "memory.md"
SESSION_FILE = ROOT / ".session_history.json"
HISTORY_FILE = ROOT / ".history"
RECENT_CONVERSATIONS_PATH = ROOT / "recent_conversations.md"

settings = SimpleNamespace(
    # Default CLI working directory; ``cli.main`` overrides it from ``--workdir``.
    workspace_dir=ROOT / "workspace",
    # Seconds to wait between two model calls.
    request_delay=1,
    # Messages kept in the sliding window sent to the model each turn.
    agent_max_messages=8,
    # Messages persisted in the session store.
    session_max_messages=6,
    # Rounds kept in recent_conversations.md.
    history_rounds=188,
)

MODEL_REQUEST_TIMEOUT_SECONDS = 600  # generous timeout for slower model generations
# Hard upper bound for synchronous command execution; the tool schema exposes
# a shorter default to the model.
TOOL_SUBPROCESS_TIMEOUT = 6 * 60
TASK_DESCRIPTION_TARGET = "[This is the task list after understanding the user's needs]"

TOTAL_TOKEN_USAGE: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}

INTERRUPTION_REQUESTED = False

_last_model_call_completed_at: float | None = None

logger = logging.getLogger("cli_agent")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_file_handler)


def wait_for_model_call_interval() -> None:
    global _last_model_call_completed_at
    if _last_model_call_completed_at is None:
        return
    elapsed = time.monotonic() - _last_model_call_completed_at
    delay = float(settings.request_delay)
    if elapsed < delay:
        time.sleep(delay - elapsed)


def mark_model_call_completed() -> None:
    global _last_model_call_completed_at
    _last_model_call_completed_at = time.monotonic()


def update_total_token_usage(usage: Any) -> None:
    if usage is None:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)):
            TOTAL_TOKEN_USAGE[key] += int(value)


def format_usage_summary(usage: Any) -> str:
    if usage is None:
        return t("token_usage_unavailable")
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    return (
        f"{t('prompt_tokens_label')}={prompt_tokens if prompt_tokens is not None else '-'} | "
        f"{t('completion_tokens_label')}={completion_tokens if completion_tokens is not None else '-'} | "
        f"{t('total_tokens_label')}={total_tokens if total_tokens is not None else '-'}"
    )


def format_cumulative_token_summary() -> str:
    return (
        f"{t('token_usage_label')} "
        f"{t('prompt_tokens_label')}={TOTAL_TOKEN_USAGE['prompt_tokens']} | "
        f"{t('completion_tokens_label')}={TOTAL_TOKEN_USAGE['completion_tokens']} | "
        f"{t('total_tokens_label')}={TOTAL_TOKEN_USAGE['total_tokens']}"
    )


def build_bottom_toolbar_text() -> str:
    return f"{format_cumulative_token_summary()} | {t('toolbar_help')}"


def handle_sigint(signum: int, frame: Any) -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = True
    raise KeyboardInterrupt(t("interrupt"))


def ensure_not_interrupted() -> None:
    if INTERRUPTION_REQUESTED:
        raise KeyboardInterrupt(t("interrupt"))


def reset_interruption_state() -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = False
