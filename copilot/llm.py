"""OpenAI-compatible chat completion wrapper with retry-friendly error mapping."""
from __future__ import annotations

import json
import traceback
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

from . import globals_
from .globals_ import (
    MODEL_REQUEST_TIMEOUT_SECONDS,
    ensure_not_interrupted,
    format_usage_summary,
    logger,
    mark_model_call_completed,
    update_total_token_usage,
    wait_for_model_call_interval,
)
from .i18n import t
from .ui import show_stage


def chat_once(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    debug_enabled: bool = False,
    disable_tools: bool = False,
) -> Any:
    """Run a single chat completion and return the assistant message.

    Errors are converted into a synthetic assistant message carrying a
    localized user-facing explanation so the caller can treat failures as
    ordinary conversation turns.
    """
    from . import tools as tools_module  # deferred to avoid circular import

    ensure_not_interrupted()
    wait_for_model_call_interval()

    tool_definitions = tools_module.TOOL_DEFINITIONS + tools_module.ACTIVE_MCP_TOOL_DEFINITIONS
    if disable_tools:
        tool_definitions = []

    request_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tool_definitions:
        request_payload["tools"] = tool_definitions
        request_payload["tool_choice"] = "auto"

    request_timeout = getattr(client, "timeout", None)
    if request_timeout is None:
        request_timeout = MODEL_REQUEST_TIMEOUT_SECONDS

    context_payload = json.dumps(request_payload, ensure_ascii=False, default=str)
    context_size_chars = len(context_payload)
    context_size_bytes = len(context_payload.encode("utf-8"))
    show_stage(
        t("start_model_call"),
        f"model={model}\nmessages={len(messages)}\n"
        f"{t('context_size_label')}={context_size_chars} chars / {context_size_bytes} bytes",
    )
    if debug_enabled:
        show_stage(t("model_request_params"), json.dumps(request_payload, ensure_ascii=False, indent=2, default=str))

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
            f"model={model}\nresponse_type={type(assistant_message).__name__}\n"
            f"{t('response_length_label')}={response_length}\n{t('token_usage_label')}={usage_summary}",
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

        if (
            "429" in err_text
            or "RateLimit" in exc_name
            or t("quota_hint") in err_text
            or "RESOURCES_TIPS" in err_text
        ):
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
