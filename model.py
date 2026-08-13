from __future__ import annotations
from cli import show_stage
from config import MODEL_REQUEST_TIMEOUT_SECONDS
from config import RE_ACTION_DELAY
from config import t
from mcp import ACTIVE_MCP_TOOL_DEFINITIONS
from openai import OpenAI
from tools import TOOL_DEFINITIONS
from tools import ensure_not_interrupted
from tools import logger
from types import SimpleNamespace
from typing import Any
import json
import time
import traceback

LAST_MODEL_CALL_COMPLETED_AT: float | None = None

TOTAL_TOKEN_USAGE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

def wait_for_model_call_interval() -> None:
    global LAST_MODEL_CALL_COMPLETED_AT
    if LAST_MODEL_CALL_COMPLETED_AT is None:
        return

    elapsed = time.monotonic() - LAST_MODEL_CALL_COMPLETED_AT
    if elapsed < RE_ACTION_DELAY:
        time.sleep(RE_ACTION_DELAY - elapsed)

def mark_model_call_completed() -> None:
    global LAST_MODEL_CALL_COMPLETED_AT
    LAST_MODEL_CALL_COMPLETED_AT = time.monotonic()

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

def chat_once(client: OpenAI, model: str, messages: list[dict[str, Any]], temperature: float, debug_enabled: bool = False, disable_tools:bool = False) -> Any:
    ensure_not_interrupted()
    wait_for_model_call_interval()
    tool_definitions = TOOL_DEFINITIONS + ACTIVE_MCP_TOOL_DEFINITIONS
    if disable_tools:
        tool_definitions = []
    request_payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "tools": tool_definitions,
        "tool_choice": "auto",
    }
    request_timeout = getattr(client, "timeout", None)
    if request_timeout is None:
        request_timeout = MODEL_REQUEST_TIMEOUT_SECONDS

    context_payload = json.dumps(request_payload, ensure_ascii=False)
    context_size_chars = len(context_payload)
    context_size_bytes = len(context_payload.encode("utf-8"))
    show_stage(t("start_model_call"), f"model={model}\nmessages={len(messages)}\n{t('context_size_label')}={context_size_chars} chars / {context_size_bytes} bytes")
    if debug_enabled:
        show_stage(t("model_request_params"), json.dumps(request_payload, ensure_ascii=False, indent=2))

    try:
        response = client.chat.completions.create(**request_payload, timeout=request_timeout)
        assistant_message = response.choices[0].message
        usage = getattr(response, "usage", None)
        if usage is not None:
            update_total_token_usage(usage)

        response_content = assistant_message.content or ""
        response_length = len(str(response_content))
        usage_summary = format_usage_summary(usage)
        show_stage(
            t("model_call_finished"),
            f"model={model}\nresponse_type={type(assistant_message).__name__}\n{t('response_length_label')}={response_length}\n{t('token_usage_label')}={usage_summary}",
        )
        if debug_enabled:
            raw_response = response.model_dump_json(indent=2)
            show_stage(t("model_raw_response"), raw_response)

        return assistant_message
    except KeyboardInterrupt:
        logger.info("Model call interrupted by user")
        show_stage(t("cancelled"), t("model_cancelled"))
        raise
    except Exception as exc:
        logger.exception(t("model_error"))
        err_text = str(exc)
        exc_name = type(exc).__name__
        status_code = getattr(exc, "status_code", None)

        if "429" in err_text or "RateLimit" in exc_name or t("quota_hint") in err_text or "RESOURCES_TIPS" in err_text:
            user_msg = t("model_quota_error")
        elif status_code == 502 or "502" in err_text or "InternalServerError" in exc_name or "Bad Gateway" in err_text:
            user_msg = t("model_502_error")
        else:
            user_msg = t("model_call_failed", error=f"{exc_name}: {err_text}")

        assistant_message = SimpleNamespace()
        assistant_message.tool_calls = []
        assistant_message.content = user_msg
        if debug_enabled:
            show_stage("Debug - model call error", traceback.format_exc())
        else:
            show_stage(t("model_call_failed", error=user_msg), user_msg)
        return assistant_message
    finally:
        mark_model_call_completed()
