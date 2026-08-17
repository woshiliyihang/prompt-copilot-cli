"""Terminal rendering helpers built on rich."""
from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel

console = Console()


def sanitize_tool_result_for_display(result: dict[str, Any], max_len: int = 80) -> dict[str, Any]:
    """Return a shallow copy of result suitable for CLI display.

    If the 'content' field is a string longer than max_len, replace it with
    a placeholder that indicates it's omitted. For lists, replace long string
    items similarly. This avoids dumping huge text blobs to the terminal.
    """
    try:
        display = dict(result)
    except Exception:
        return {"status": "error", "content": "<unserializable result>"}

    content = display.get("content")
    if isinstance(content, str):
        if len(content) > max_len:
            display["content"] = f"<content omitted: length={len(content)} chars>"
    elif isinstance(content, list):
        new_list: list[Any] = []
        for item in content:
            if isinstance(item, str) and len(item) > max_len:
                new_list.append(f"<item omitted: length={len(item)} chars>")
            else:
                new_list.append(item)
        display["content"] = new_list
    return display


def show_stage(title: str, content: str) -> None:
    """Print a rich panel; sanitises embedded ``result={json}`` payloads."""
    if "\nresult=" in content:
        prefix, json_part = content.split("\nresult=", 1)
        try:
            parsed = json.loads(json_part)
            sanitized = sanitize_tool_result_for_display(parsed)
            content = f"{prefix}\nresult={json.dumps(sanitized, ensure_ascii=False)}"
        except Exception:
            # If it's not valid JSON, leave it as-is
            pass
    console.print(Panel.fit(content, title=title))


def show_tool_result(tool_call: Any, result: dict[str, Any]) -> None:
    # Imported lazily to avoid a circular import between tools and ui.
    from .tools import get_tool_description
    from .i18n import t

    display_result = sanitize_tool_result_for_display(result)
    description = get_tool_description(tool_call)
    description_text = f"{description}\n\n" if description else ""
    console.print(
        Panel.fit(
            f"[bold cyan]{tool_call.function.name}[/bold cyan]\n{description_text}"
            f"{json.dumps(display_result, ensure_ascii=False, indent=2)}",
            title=t("tool_result_title"),
        )
    )
