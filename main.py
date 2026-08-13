from __future__ import annotations

from cli import build_cli_parser
from cli import interactive_loop
from cli import run_agent
from config import DEFAULT_MAX_CHAT_COUNT
from config import LOG_DIR
from config import LOG_FILE
from config import ROOT
from config import WORKSPACE_DIR
from config import build_client
from config import ensure_config
from config import t
from mcp import console
from mcp import discover_mcp_tools
from session import ConversationRecorder
from session import SessionStore
from tools import logger

import argparse
import asyncio
import base64
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import OpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from types import SimpleNamespace


ROOT.mkdir(parents=True, exist_ok=True)
































logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)


def handle_sigint(signum: int, frame: Any) -> None:
    global INTERRUPTION_REQUESTED
    INTERRUPTION_REQUESTED = True
    raise KeyboardInterrupt(t("interrupt"))

























































import os
import signal
import subprocess
import uuid
import time
import platform
from pathlib import Path

























import json
from typing import List, Union










def main() -> None:
    global UI_SYSTEM_LANGUAGE, DEFAULT_SYSTEM_PROMPT, WORKSPACE_DIR, RE_ACTION_DELAY, DEFAULT_MAX_CHAT_COUNT, CHAT_MESSAGE_MAX_COUNT

    parser = build_cli_parser()
    args = parser.parse_args()

    RE_ACTION_DELAY = int(args.request_delay)
    DEFAULT_MAX_CHAT_COUNT = int(args.history_count)
    CHAT_MESSAGE_MAX_COUNT = int(args.agent_messages_count)

    # Set localization language
    UI_SYSTEM_LANGUAGE = args.lang

    # set up workspace directory
    WORKSPACE_DIR = Path(args.workdir)
    console.print(Panel.fit(t("workdir_message", path=WORKSPACE_DIR), title=t("cli_config_title")))

    try:
        model_cfg, system_prompt = ensure_config(workdir=WORKSPACE_DIR)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WORKSPACE_DIR)

    signal.signal(signal.SIGINT, handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigint)

    mcp_tools = discover_mcp_tools(model_cfg)
    if mcp_tools:
        console.print(Panel.fit(t("mcp_connected", count=len(mcp_tools)), title=t("mcp_config_title")))

    try:
        client = build_client(model_cfg)
    except RuntimeError as exc:
        console.print(Panel.fit(str(exc), title=t("config_error_title")))
        return

    session_file = ROOT / ".session_history.json"
    history_file = ROOT / ".history"

    session_store = SessionStore(session_file, max_messages=DEFAULT_MAX_CHAT_COUNT)
    if args.reset_session:
        session_store.save([])
        recent_conversations_path = ROOT / "recent_conversations.md"
        if recent_conversations_path.exists():
            recent_conversations_path.write_text(t("recent_conversations_header") + "\n\n", encoding="utf-8")
        console.print(t("session_reset_message"))

    debug_enabled = bool(model_cfg.get("debug", False))
    if debug_enabled:
        console.print(Panel.fit(t("debug_enabled_message"), title=t("debug_config_title")))

    # Create conversation recorder to persist recent rounds to markdown
    recorder = ConversationRecorder(ROOT / "recent_conversations.md", max_rounds=188)

    if args.task:
        try:
            run_agent(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, args.task, debug_enabled=debug_enabled, recorder=recorder)
        except KeyboardInterrupt:
            console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
        return

    try:
        interactive_loop(client, model_cfg.get("model", "gpt-4o-mini"), system_prompt, session_store, history_file, debug_enabled=debug_enabled, recorder=recorder)
    except KeyboardInterrupt:
        console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        console.print(Panel.fit(traceback.format_exc(), title=t("runtime_error_title")))
        sys.exit(1)
