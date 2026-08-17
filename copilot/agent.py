"""Agent orchestration: planning, tool loop, conversation recording."""
from __future__ import annotations

import base64
import inspect
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

from .config import memory_settings
from .globals_ import (
    MEMORY_FILE_PATH,
    logger,
    reset_interruption_state,
    settings,
)
from .i18n import t
from .llm import chat_once
from .memory import auto_extract_memories, default_store, format_memories_for_prompt
from .prompts import (
    append_task_memory_entry,
    build_task_end_system_prompt,
    clear_task_memory_file,
    summarize_memory_value,
    summarize_tool_result,
)
from .session import SessionStore
from .tools import execute_tool_call
from .ui import console, show_stage, show_tool_result


# --------------------------------------------------------------------------
# Multimodal helpers
# --------------------------------------------------------------------------


def build_multimodal_user_message(
    text: str,
    image_path: str | os.PathLike[str],
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Build a user message embedding *image_path* as a data URL.

    Large images are progressively downscaled/recompressed with Pillow when it
    is available so the base64 payload stays within *max_bytes*.
    """
    path = Path(image_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_bytes = path.read_bytes()
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"

    if max_bytes is None:
        max_bytes = 120_000

    if max_bytes and len(image_bytes) > max_bytes:
        try:
            import io

            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGB")
                candidate_bytes = image_bytes
                candidate_mime = mime_type
                width, height = img.size
                for scale in [1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05, 0.03, 0.01]:
                    resized = img.resize(
                        (max(16, int(width * scale)), max(16, int(height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                    for quality in [85, 70, 55, 40, 25, 15, 10]:
                        buffer = io.BytesIO()
                        resized.save(buffer, format="JPEG", quality=quality, optimize=True)
                        candidate_bytes = buffer.getvalue()
                        candidate_mime = "image/jpeg"
                        if len(base64.b64encode(candidate_bytes).decode("ascii")) <= max_bytes:
                            image_bytes = candidate_bytes
                            mime_type = candidate_mime
                            break
                    if len(base64.b64encode(candidate_bytes).decode("ascii")) <= max_bytes:
                        break
                else:
                    tiny = img.resize((32, 32), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    tiny.save(buffer, format="JPEG", quality=15, optimize=True)
                    image_bytes = buffer.getvalue()
                    mime_type = "image/jpeg"
                    if len(base64.b64encode(image_bytes).decode("ascii")) > max_bytes:
                        image_bytes = b"\x00"
                        mime_type = "image/jpeg"
        except Exception:
            image_bytes = image_bytes[: max(1, max_bytes // 2)]

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ],
    }


# --------------------------------------------------------------------------
# Conversation recording
# --------------------------------------------------------------------------


class ConversationRecorder:
    """Append-only markdown recorder of recent rounds, capped at *max_rounds*."""

    def __init__(self, md_path: Path, max_rounds: int = 50):
        self.md_path = md_path
        self.max_rounds = max_rounds
        self.md_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.md_path.exists():
            self.md_path.write_text(t("recent_conversations_header") + "\n\n", encoding="utf-8")

    def _append(self, text: str) -> None:
        with self.md_path.open("a", encoding="utf-8") as f:
            f.write(text)
        self._trim_rounds()

    def _trim_rounds(self) -> None:
        content = self.md_path.read_text(encoding="utf-8")
        parts = content.split("\n## Round ")
        if len(parts) <= self.max_rounds + 1:
            return
        header = parts[0]
        rounds = parts[1:]
        keep = rounds[-self.max_rounds :]
        new_content = header + "\n## Round " + "\n## Round ".join(keep)
        self.md_path.write_text(new_content, encoding="utf-8")

    def start_round(self, user_text: str) -> None:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        header = f"\n## Round {ts}\n\n"
        user_block = f"**User:**\n\n{user_text}\n\n"
        self._append(header + user_block)

    def record_assistant(self, assistant_text: str) -> None:
        self._append(f"**Assistant:**\n\n{assistant_text}\n\n")

    def record_tool_start(self, tool_name: str, args: Any) -> None:
        try:
            args_text = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_text = str(args)
        self._append(f"**Tool Start:** {tool_name}\n\nArguments: {args_text}\n\n")

    def record_tool_result(self, tool_name: str, result: dict[str, Any]) -> None:
        status = result.get("status")
        content = result.get("content")
        try:
            content_text = json.dumps(content, ensure_ascii=False)
        except Exception:
            content_text = str(content)
        self._append(f"**Tool Result:** {tool_name} (status={status})\n\n{content_text}\n\n")

    def record_error(self, error_text: str) -> None:
        self._append(f"**Error:**\n\n{error_text}\n\n")


# --------------------------------------------------------------------------
# Planning and special commands
# --------------------------------------------------------------------------


def is_special_command(user_text: str) -> bool:
    normalized = (user_text or "").strip()
    if not normalized:
        return False
    special_commands = {"/exit", "/clear", "/task-start", "/task-end", "/memory"}
    return normalized in special_commands


def trim_messages_window(messages: list[dict[str, Any]], max_messages: int) -> list[dict[str, Any]]:
    """Return the last *max_messages* messages without orphaning tool results.

    OpenAI-compatible APIs require every ``tool`` message to follow the
    ``assistant`` message carrying its ``tool_calls``.  A naive tail slice can
    start in the middle of such a group, so leading orphaned tool messages
    (and a dangling assistant-with-tool_calls message lacking its results) are
    dropped from the window.
    """
    if len(messages) <= max_messages:
        window = list(messages)
    else:
        window = list(messages[-max_messages:])

    # Drop leading tool messages whose preceding assistant call fell outside
    # the window.
    while window and window[0].get("role") == "tool":
        window.pop(0)

    # If the window now starts with an assistant message that has tool_calls,
    # its results may have been dropped by the slice; keep it only when at
    # least one matching tool result is still present.
    if window and window[0].get("role") == "assistant" and window[0].get("tool_calls"):
        call_ids = {
            str(call.get("id"))
            for call in window[0].get("tool_calls", [])
            if isinstance(call, dict)
        }
        has_results = any(
            message.get("role") == "tool" and str(message.get("tool_call_id")) in call_ids
            for message in window[1:]
        )
        if not has_results:
            window.pop(0)
            while window and window[0].get("role") == "tool":
                window.pop(0)

    return window


def _call_chat_fn(
    chat_fn: Any,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    debug_enabled: bool,
) -> Any:
    """Invoke *chat_fn*, passing ``disable_tools`` only when it is accepted.

    Production chat functions accept ``disable_tools``; test fakes may not, so
    the signature is inspected before deciding which keyword arguments to use.
    """
    try:
        accepts_disable_tools = "disable_tools" in inspect.signature(chat_fn).parameters
    except (TypeError, ValueError):
        accepts_disable_tools = False

    if accepts_disable_tools:
        return chat_fn(
            client,
            model,
            messages,
            temperature=temperature,
            debug_enabled=debug_enabled,
            disable_tools=True,
        )
    return chat_fn(client, model, messages, temperature=temperature, debug_enabled=debug_enabled)


def plan_user_request(
    client: OpenAI,
    model: str,
    history: list[dict[str, Any]],
    user_text: str,
    debug_enabled: bool = False,
    chat_fn: Any | None = None,
) -> str:
    """Generate an execution checklist for *user_text* via the planning model.

    ``chat_fn`` lets callers (notably the ``main`` module, whose attribute is
    monkeypatched in tests) inject an alternative model-call function.  When
    omitted the package's :func:`chat_once` is used with tools disabled.

    The planning prompt instructs the model to classify commands first:
    短时命令 run synchronously and are judged by their exit code; 持久命令 run
    in the background and are confirmed via logs / health checks.
    """
    from .prompts import PLANNING_PROMPT

    if is_special_command(user_text):
        return user_text

    planning_prompt = PLANNING_PROMPT.format(memory_file_path=MEMORY_FILE_PATH)
    planning_messages: list[dict[str, Any]] = [
        {"role": "system", "content": planning_prompt},
    ]
    if history:
        planning_messages.append(
            {"role": "user", "content": "以下是历史上下文：\n" + json.dumps(history, ensure_ascii=False)}
        )
    planning_messages.append({"role": "user", "content": user_text})

    if chat_fn is not None:
        assistant_message = chat_fn(
            client, model, planning_messages, temperature=0.2, debug_enabled=debug_enabled
        )
    else:
        assistant_message = chat_once(
            client,
            model,
            planning_messages,
            temperature=0.2,
            debug_enabled=debug_enabled,
            disable_tools=True,
        )
    return (assistant_message.content or "").strip() or user_text


def to_tool_call_objects(tool_calls: list[Any]) -> list[Any]:
    converted: list[Any] = []
    for idx, tool_call in enumerate(tool_calls):
        if isinstance(tool_call, SimpleNamespace):
            converted.append(tool_call)
            continue

        function = getattr(tool_call, "function", None)
        if function is None:
            continue

        converted.append(
            SimpleNamespace(
                id=getattr(tool_call, "id", f"tool_call_{idx}"),
                type=getattr(tool_call, "type", "function"),
                function=SimpleNamespace(
                    name=getattr(function, "name", ""),
                    arguments=getattr(function, "arguments", {}),
                    description=getattr(function, "description", ""),
                ),
            )
        )
    return converted


def parse_tool_calls_from_content(content: str | None) -> list[Any]:
    """Fallback: recover tool calls embedded as JSON objects in plain content."""
    if not content:
        return []

    text = content.strip()
    if not text:
        return []

    decoded_calls: list[Any] = []
    start = 0
    while True:
        start = text.find("{", start)
        if start == -1:
            break

        try:
            parsed, end = json.JSONDecoder().raw_decode(text[start:])
        except Exception:
            start += 1
            continue

        if isinstance(parsed, dict) and {"name", "arguments"}.issubset(parsed.keys()):
            name = str(parsed.get("name") or "").strip()
            arguments = parsed.get("arguments") or {}
            if name:
                decoded_calls.append(
                    SimpleNamespace(
                        id=f"content_tool_{len(decoded_calls)}",
                        type="function",
                        function=SimpleNamespace(name=name, arguments=arguments),
                    )
                )
            start += end
            continue

        start += 1

    return decoded_calls


# --------------------------------------------------------------------------
# Memory-aware message assembly
# --------------------------------------------------------------------------


def build_memory_context_messages(
    client: OpenAI,
    model: str,
    model_cfg: dict[str, Any],
    user_text: str,
    language: str,
) -> list[dict[str, Any]]:
    """Retrieve relevant long-term memories and return them as messages."""
    memory_cfg = memory_settings(model_cfg)
    if not memory_cfg["enabled"] or memory_cfg["max_results"] <= 0:
        return []
    try:
        memories = default_store().search(user_text, limit=memory_cfg["max_results"])
    except Exception:
        logger.exception("Long-term memory retrieval failed")
        return []
    block = format_memories_for_prompt(memories)
    if not block:
        return []
    return [{"role": "system", "content": block}]


def _maybe_auto_extract_memories(
    client: OpenAI,
    model: str,
    model_cfg: dict[str, Any],
    language: str,
    final_answer: str,
    user_text: str,
    tool_trace: list[str],
) -> None:
    memory_cfg = memory_settings(model_cfg)
    if not (memory_cfg["enabled"] and memory_cfg["auto_extract"]):
        return
    transcript_parts = [f"User request: {user_text}"]
    transcript_parts.extend(tool_trace[-12:])
    if final_answer:
        transcript_parts.append(f"Final answer: {final_answer[-3000:]}")
    transcript = "\n\n".join(transcript_parts)
    try:
        auto_extract_memories(client, model, transcript, language)
    except Exception:
        logger.exception("Auto memory extraction failed")


# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------


def run_agent(
    client: OpenAI,
    model: str,
    system_prompt: str,
    session_store: SessionStore,
    user_text: str,
    debug_enabled: bool = False,
    recorder: ConversationRecorder | None = None,
    model_cfg: dict[str, Any] | None = None,
    language: str = "en",
) -> None:
    """Run one full agent turn: plan -> loop(model/tools) -> answer -> memorize."""
    from rich.panel import Panel

    reset_interruption_state()
    clear_task_memory_file()
    append_task_memory_entry(
        f"## Task started\n\nUser request: {summarize_memory_value(user_text, max_len=240)}\n\n"
    )

    effective_cfg = model_cfg or {}
    history = session_store.load()
    planned_user_text = user_text
    if not is_special_command(user_text):
        try:
            planned_user_text = plan_user_request(
                client, model, history, user_text, debug_enabled=debug_enabled
            )
        except Exception:
            logger.exception("Planning step failed; continuing with original user input")

    append_task_memory_entry(
        "## Task checklist\n\n" + summarize_memory_value(planned_user_text, max_len=10000) + "\n\n"
    )

    first_task_prompt = {"role": "user", "content": planned_user_text}
    history.append(first_task_prompt)
    session_store.save(history)
    if recorder:
        recorder.start_round(user_text)

    system_prompt_message = {"role": "system", "content": system_prompt}
    memory_messages: list[dict[str, Any]] = []
    if not is_special_command(user_text):
        try:
            memory_messages = build_memory_context_messages(
                client, model, effective_cfg, user_text, language
            )
        except Exception:
            logger.exception("Memory context retrieval failed")

    messages: list[dict[str, Any]] = []
    messages.extend(history)

    tool_trace: list[str] = []
    final_answer = ""

    # Hard cap on tool-loop iterations so a model that keeps requesting tools
    # cannot run forever.
    max_iterations = 30
    iteration = 0

    while True:
        iteration += 1
        if iteration > max_iterations:
            logger.warning("Agent tool loop stopped after %d iterations", max_iterations)
            final_answer = "Reached the maximum number of tool iterations without a final answer."
            history.append({"role": "assistant", "content": final_answer})
            session_store.save(history)
            console.print(Panel.fit(final_answer, title="Agent reply"))
            if recorder:
                recorder.record_assistant(final_answer)
            return

        try:
            finalize_prompt: list[dict[str, Any]] = []
            # Keep only the last N messages for context, without orphaning
            # tool-result messages.
            messages = trim_messages_window(messages, settings.agent_max_messages)
            finalize_prompt.extend(messages)
            if first_task_prompt not in finalize_prompt:
                finalize_prompt = [first_task_prompt] + finalize_prompt
            finalize_prompt = [system_prompt_message] + memory_messages + finalize_prompt
            assistant_message = chat_once(
                client, model, finalize_prompt, temperature=0.2, debug_enabled=debug_enabled
            )
        except KeyboardInterrupt:
            logger.info("Current task interrupted by user")
            console.print(Panel.fit(t("task_cancelled"), title=t("interrupted_title")))
            if recorder:
                recorder.record_error("User interrupted the current task")
            return
        except Exception:
            logger.exception("Chat request failed")
            console.print(Panel.fit(traceback.format_exc(), title=t("runtime_error_title")))
            if recorder:
                recorder.record_error(traceback.format_exc())
            return

        tool_calls = to_tool_call_objects(list(getattr(assistant_message, "tool_calls", []) or []))
        if not tool_calls:
            tool_calls = parse_tool_calls_from_content(getattr(assistant_message, "content", ""))

        if not tool_calls:
            answer = assistant_message.content or "I did not receive a valid reply."
            final_answer = str(answer)
            history.append({"role": "assistant", "content": answer})
            session_store.save(history)
            console.print(Panel.fit(answer, title="Agent reply"))
            if recorder:
                recorder.record_assistant(answer)
            _maybe_auto_extract_memories(
                client, model, effective_cfg, language, final_answer, user_text, tool_trace
            )
            return

        assistant_call_message = {
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_call_message)

        function_content = getattr(assistant_message, "content", None) or ""
        function_reasoning = getattr(assistant_message, "reasoning", None) or ""
        function_content_reasoning = getattr(assistant_message, "content_reasoning", None) or ""
        final_reasoning = function_reasoning or function_content_reasoning or function_content
        append_task_memory_entry(
            "### The reason for the assistant to call the function\n\n" f"{final_reasoning}\n\n"
        )
        show_stage(t("starting_tool_call"), f"reasoning:\n{final_reasoning}")

        for tc in tool_calls:
            try:
                result = execute_tool_call(tc)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                show_tool_result(tc, result)
                tool_trace.append(
                    f"Tool {tc.function.name}: {summarize_tool_result(result)[:500]}"
                )
                append_task_memory_entry(
                    "### Tool invocation\n\n"
                    f"Tool: {tc.function.name}\n\n"
                    f"Arguments: {summarize_memory_value(tc.function.arguments, max_len=1200)}\n\n"
                    f"Result: {summarize_tool_result(result)}\n\n"
                )

                if tc.function.name == "read_image_as_base64":
                    try:
                        payload = (
                            json.loads(result.get("content", "{}"))
                            if isinstance(result.get("content"), str)
                            else result.get("content", {})
                        )
                        image_path = payload.get("path") if isinstance(payload, dict) else None
                        if image_path:
                            image_message = build_multimodal_user_message(
                                "The user attached an image for multimodal analysis. Please inspect it carefully.",
                                image_path,
                                max_bytes=120_000,
                            )
                            messages.append(image_message)
                    except Exception:
                        logger.exception("Failed to append multimodal image message")
            except KeyboardInterrupt:
                logger.info("Tool execution interrupted by user, tool=%s", tc.function.name)
                console.print(Panel.fit(t("current_tool_cancelled"), title=t("interrupted_title")))
                return
            except Exception:
                logger.exception("Tool execution error, tool=%s", tc.function.name)
                error_payload = {"status": "error", "content": traceback.format_exc()}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(error_payload, ensure_ascii=False),
                    }
                )
                console.print(Panel.fit(traceback.format_exc(), title=t("tool_execution_error")))
                append_task_memory_entry(
                    "### Tool invocation\n\n"
                    f"Tool: {tc.function.name}\n\n"
                    f"Arguments: {summarize_memory_value(tc.function.arguments, max_len=1200)}\n\n"
                    f"Result: ERROR\n{summarize_tool_result(error_payload)}\n\n"
                )


# --------------------------------------------------------------------------
# /task-end
# --------------------------------------------------------------------------


def get_content_from_tool_calls(tool_calls: Any) -> str:
    """Extract the ``content`` argument from the first tool call that has one."""
    if not tool_calls:
        return ""

    for tool_call in tool_calls:
        try:
            if isinstance(tool_call, dict):
                func = tool_call.get("function")
                if not func:
                    continue
                arguments_str = (
                    func.get("arguments") if isinstance(func, dict) else getattr(func, "arguments", None)
                )
            else:
                func = getattr(tool_call, "function", None)
                if func is None:
                    continue
                arguments_str = getattr(func, "arguments", None)

            if not arguments_str:
                continue

            if isinstance(arguments_str, str):
                arguments = json.loads(arguments_str)
            elif isinstance(arguments_str, dict):
                arguments = arguments_str
            else:
                continue

            if isinstance(arguments, dict) and "content" in arguments:
                return arguments["content"]

        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    return ""


def handle_task_end_command(
    md_path: Path,
    client: OpenAI,
    model: str,
    system_prompt: str,
    workdir: Path,
    debug_enabled: bool = False,
    language: str = "en",
    chat_fn: Any | None = None,
) -> None:
    """Find the most recent /task-start round and generate a refined final prompt."""
    from rich.panel import Panel

    if not md_path.exists():
        console.print(Panel.fit(t("task_end_file_missing", path=md_path), title=t("task_end_error")))
        return

    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("\n## Round ")
    if len(parts) <= 1:
        console.print(Panel.fit(t("task_end_empty"), title=t("task_end_error")))
        return

    rounds = parts[1:]

    def extract_user_text(part: str) -> str:
        marker = "**User:**"
        idx = part.find(marker)
        if idx == -1:
            return ""
        sub = part[idx + len(marker) :]
        end_idx = sub.find("\n\n**")
        if end_idx == -1:
            end_idx = len(sub)
        return sub[:end_idx].strip()

    start_index = None
    for i in range(len(rounds) - 1, -1, -1):
        user_text = extract_user_text(rounds[i])
        if "/task-start" in user_text:
            start_index = i
            break

    if start_index is None:
        console.print(Panel.fit(t("task_end_no_task_start"), title=t("task_end_notice")))
        return

    selected = rounds[start_index:]
    compiled_text = "\n\n".join(["## Round " + r for r in selected])

    sys_prompt = build_task_end_system_prompt(language)
    user_prompt = t("task_end_user_prompt", compiled_text=compiled_text)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    effective_chat_fn = chat_fn if chat_fn is not None else chat_once
    try:
        assistant_message = _call_chat_fn(
            effective_chat_fn, client, model, messages, temperature=0.2, debug_enabled=debug_enabled
        )
    except Exception:
        logger.exception(t("task_end_generation_failed_log"))
        console.print(Panel.fit(traceback.format_exc(), title=t("task_end_generate_failed")))
        return

    prompt_call_tools = get_content_from_tool_calls(getattr(assistant_message, "tool_calls", None))
    final_prompt = (assistant_message.content or "").strip()
    if not final_prompt:
        final_prompt = prompt_call_tools
    if not final_prompt:
        console.print(Panel.fit(t("task_end_no_prompt"), title=t("task_end_result")))
        return

    out_path = Path(workdir) / "last-prompt.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_text = t("final_prompt_header") + "\n\n" + final_prompt + "\n"
    out_path.write_text(out_text, encoding="utf-8")
    console.print(Panel.fit(t("task_end_completed", path=out_path), title=t("task_end_done")))
