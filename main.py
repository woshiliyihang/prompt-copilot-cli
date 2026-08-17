"""Prompt Copilot CLI — thin entry module.

The implementation lives in the ``copilot`` package; this module re-exports
the public API (including a few compatibility shims used by the test-suite)
and provides the console entry point.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# -- i18n ----------------------------------------------------------------
from copilot.i18n import TRANSLATIONS, set_language, t
from copilot import i18n as _i18n

UI_SYSTEM_LANGUAGE = _i18n.UI_SYSTEM_LANGUAGE

# -- globals -------------------------------------------------------------
from copilot import globals_
from copilot.globals_ import (
    APPLICATION_VERSION,
    HISTORY_FILE,
    INTERRUPTION_REQUESTED,
    LOG_DIR,
    LOG_FILE,
    MEMORY_DB_PATH,
    MEMORY_FILE_PATH,
    MODEL_REQUEST_TIMEOUT_SECONDS,
    RECENT_CONVERSATIONS_PATH,
    ROOT,
    SESSION_FILE,
    TASK_DESCRIPTION_TARGET,
    TOOL_SUBPROCESS_TIMEOUT,
    TOTAL_TOKEN_USAGE,
    build_bottom_toolbar_text,
    format_cumulative_token_summary,
    format_usage_summary,
    handle_sigint,
    mark_model_call_completed,
    reset_interruption_state,
    update_total_token_usage,
    wait_for_model_call_interval,
)

# -- prompts --------------------------------------------------------------
from copilot.prompts import (
    DEFAULT_SYSTEM_WORKING_PRINCIPLES_EN,
    DEFAULT_SYSTEM_WORKING_PRINCIPLES_ZH,
    PLANNING_PROMPT,
    append_task_memory_entry,
    build_device_environment_context,
    build_system_prompt,
    build_task_end_system_prompt,
    clear_task_memory_file,
    get_working_principles,
    summarize_memory_value,
    summarize_tool_result,
)

# -- config ----------------------------------------------------------------
from copilot.config import (
    CONFIG_SAVE_FILE_PATH,
    DEFAULT_MODEL_CONFIG,
    build_client,
    config_field_descriptions,
    ensure_config,
    memory_settings,
)

# -- session ----------------------------------------------------------------
from copilot.session import SessionStore

# -- memory ------------------------------------------------------------------
from copilot.memory import (
    MAX_MEMORY_CHARS,
    MemoryStore,
    auto_extract_memories,
    default_store,
    format_memories_for_prompt,
    parse_extracted_memories,
    reset_default_store,
)

# -- tools -------------------------------------------------------------------
from copilot import tools as _tools
from copilot.tools import (
    ACTIVE_MCP_TOOL_CONFIG,
    ACTIVE_MCP_TOOL_CONFIGS,
    ACTIVE_MCP_TOOL_DEFINITIONS,
    ACTIVE_MCP_TOOL_SERVER_BY_NAME,
    get_tool_description,
    handle_execute_command,
    handle_memory_add,
    handle_memory_delete,
    handle_memory_list,
    handle_memory_search,
    looks_like_background_service_command,
    refresh_tool_definitions,
    resolve_execution_cwd,
    run_subprocess_command,
    safe_parse_tool_args,
    start_background_process,
    stream_background_process_output,
    wait_for_health_check,
)

TOOL_DEFINITIONS = _tools.TOOL_DEFINITIONS

# -- mcp ----------------------------------------------------------------------
from copilot.mcp import (
    discover_mcp_tools,
    normalize_mcp_server_config,
    normalize_mcp_server_configs,
    normalize_mcp_tool_definition,
    run_mcp_tool,
)

# -- llm ----------------------------------------------------------------------
from copilot.llm import chat_once as _chat_once_impl

# -- agent ----------------------------------------------------------------------
from copilot.agent import (
    ConversationRecorder,
    build_memory_context_messages,
    build_multimodal_user_message,
    get_content_from_tool_calls,
    is_special_command,
    parse_tool_calls_from_content,
    plan_user_request as _plan_user_request_impl,
    run_agent,
    to_tool_call_objects,
)
from copilot.agent import handle_task_end_command as _handle_task_end_impl

# -- cli ----------------------------------------------------------------------
from copilot.cli import SlashCommandCompleter, build_cli_parser, interactive_loop
from copilot.cli import main as cli_main
from copilot.ui import console, sanitize_tool_result_for_display, show_stage, show_tool_result


# --------------------------------------------------------------------------
# Compatibility shims (the test-suite monkeypatches module attributes)
# --------------------------------------------------------------------------


def chat_once(client: Any, model: str, messages: list[dict[str, Any]], temperature: float, debug_enabled: bool = False) -> Any:
    """Module-level model call used by planning/task-end shims.

    Tests replace this attribute; the wrappers below resolve it dynamically so
    a replacement always takes effect.
    """
    return _chat_once_impl(client, model, messages, temperature, debug_enabled=debug_enabled)


def plan_user_request(
    client: Any,
    model: str,
    history: list[dict[str, Any]],
    user_text: str,
    debug_enabled: bool = False,
) -> str:
    """Generate an execution checklist for *user_text*.

    命令执行方式分类指引：规划步骤涉及执行命令时必须先分类——
    短时命令（如 ls、pytest、npm run build 等一次性执行并退出的命令）同步等待，
    依据退出码判断成败；持久命令（如 npm run dev、flask run 等常驻服务命令）
    后台执行，通过日志与健康检查确认就绪，不要同步等待。

    长期记忆采用“记事本方式”管理：跨会话的重要信息用 memory_search / memory_add
    读写长期记忆库；任务内的中间信息自动记录到任务记事本文件。
    """
    from copilot.globals_ import MEMORY_FILE_PATH as _memory_file_path
    import json as _json

    if is_special_command(user_text):
        return user_text

    planning_prompt = PLANNING_PROMPT.format(memory_file_path=_memory_file_path)
    planning_messages: list[dict[str, Any]] = [
        {"role": "system", "content": planning_prompt},
    ]
    if history:
        planning_messages.append(
            {"role": "user", "content": "以下是历史上下文：\n" + _json.dumps(history, ensure_ascii=False)}
        )
    planning_messages.append({"role": "user", "content": user_text})

    # Resolve chat_once from module globals so monkeypatching takes effect.
    chat_fn = globals().get("chat_once", _chat_once_impl)
    assistant_message = chat_fn(client, model, planning_messages, temperature=0.2, debug_enabled=debug_enabled)
    return (assistant_message.content or "").strip() or user_text


def handle_task_end_command(
    md_path: Path,
    client: Any,
    model: str,
    system_prompt: str,
    workdir: Path,
    debug_enabled: bool = False,
    language: str | None = None,
) -> None:
    """/task-end handler honoring the module-level ``UI_SYSTEM_LANGUAGE``."""
    lang = language if language is not None else globals().get("UI_SYSTEM_LANGUAGE", _i18n.UI_SYSTEM_LANGUAGE)
    set_language(lang)
    chat_fn = globals().get("chat_once", _chat_once_impl)
    _handle_task_end_impl(
        md_path,
        client,
        model,
        system_prompt,
        workdir,
        debug_enabled=debug_enabled,
        language=lang,
        chat_fn=chat_fn,
    )


def execute_tool_call(tool_call: Any) -> dict[str, Any]:
    """Execute one tool call; resolves ``start_background_process`` dynamically
    so tests can monkeypatch this module's attribute."""
    starter = globals().get("start_background_process", start_background_process)
    return _tools.execute_tool_call(tool_call, background_starter=starter)


def main() -> None:
    cli_main()


if __name__ == "__main__":
    import sys
    import traceback as _traceback

    from rich.panel import Panel as _Panel

    try:
        main()
    except Exception:
        console.print(_Panel.fit(_traceback.format_exc(), title=t("runtime_error_title")))
        sys.exit(1)
