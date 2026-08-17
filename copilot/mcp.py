"""MCP (Model Context Protocol) server integration: discovery and invocation."""
from __future__ import annotations

import asyncio
import traceback
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from . import tools as tools_module
from .globals_ import logger
from .i18n import t
from .ui import console


def normalize_mcp_tool_definition(tool_obj: Any) -> dict[str, Any]:
    schema = getattr(tool_obj, "inputSchema", None) or {}
    parameters = dict(schema)
    if not parameters.get("type"):
        parameters["type"] = "object"
    if parameters.get("type") == "object" and not parameters.get("properties"):
        parameters["properties"] = {}

    return {
        "type": "function",
        "function": {
            "name": getattr(tool_obj, "name", ""),
            "description": getattr(tool_obj, "description", "") or "",
            "parameters": parameters,
        },
    }


async def _run_mcp_session(server_config: dict[str, Any], handler: Any) -> Any:
    transport = str(server_config.get("transport") or server_config.get("type") or "stdio").lower()
    if transport in {"http", "streamable_http", "streamable-http"}:
        url = server_config.get("url")
        headers = server_config.get("headers") or {}
        if not url:
            raise RuntimeError("MCP HTTP server is missing a URL")
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await handler(session)

    if transport == "sse":
        url = server_config.get("url")
        headers = server_config.get("headers") or {}
        if not url:
            raise RuntimeError("MCP SSE server is missing a URL")
        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await handler(session)

    command = server_config.get("command") or "npx"
    args = list(server_config.get("args") or [])
    server_params = StdioServerParameters(command=command, args=args)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await handler(session)


def normalize_mcp_server_config(raw_config: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(fallback or {})
    if isinstance(raw_config, dict):
        merged = dict(base)
        merged.update({k: v for k, v in raw_config.items() if v is not None})
        transport = str(merged.get("transport") or merged.get("type") or base.get("transport") or "stdio").lower()
        if transport in {"sse", "http", "streamable_http", "streamable-http"}:
            headers = merged.get("headers") or base.get("headers") or {}
            headers = dict(headers) if isinstance(headers, dict) else {}
            url = merged.get("url") or base.get("url") or ""
            return {
                "name": merged.get("name") or url or f"mcp-{transport}",
                "transport": transport,
                "url": url,
                "headers": headers,
            }

        command = merged.get("command") or base.get("command") or "npx"
        args_value = merged.get("args") or merged.get("arguments") or base.get("args") or []
        if isinstance(args_value, str):
            args = [args_value]
        elif isinstance(args_value, list):
            args = list(args_value)
        else:
            args = [str(args_value)]
        return {
            "name": merged.get("name") or f"{command}:{' '.join(args)}",
            "transport": transport,
            "command": command,
            "args": args,
        }

    if isinstance(raw_config, str):
        return {"name": raw_config, "transport": "stdio", "command": raw_config, "args": []}

    return {"name": "mcp", "transport": "stdio", "command": "npx", "args": []}


def normalize_mcp_server_configs(mcp_cfg: Any) -> list[dict[str, Any]]:
    if isinstance(mcp_cfg, list):
        return [normalize_mcp_server_config(item) for item in mcp_cfg if item is not None]

    if isinstance(mcp_cfg, dict):
        if isinstance(mcp_cfg.get("servers"), list):
            fallback = {
                "command": mcp_cfg.get("command"),
                "args": mcp_cfg.get("args"),
                "transport": mcp_cfg.get("transport") or mcp_cfg.get("type"),
                "url": mcp_cfg.get("url"),
                "headers": mcp_cfg.get("headers"),
            }
            return [
                normalize_mcp_server_config(item, fallback=fallback)
                for item in mcp_cfg["servers"]
                if item is not None
            ]
        return [normalize_mcp_server_config(mcp_cfg)]

    return []


def _reset_mcp_state() -> None:
    tools_module.ACTIVE_MCP_TOOL_DEFINITIONS = []
    tools_module.ACTIVE_MCP_TOOL_CONFIG = {}
    tools_module.ACTIVE_MCP_TOOL_CONFIGS = []
    tools_module.ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}


def discover_mcp_tools(model_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Connect to configured MCP servers and register their tools."""
    from rich.panel import Panel

    mcp_cfg = model_cfg.get("mcp", {})
    enabled = bool(mcp_cfg.get("enabled", True)) if isinstance(mcp_cfg, dict) else True
    if not enabled:
        _reset_mcp_state()
        return []

    server_configs = normalize_mcp_server_configs(mcp_cfg)
    if not server_configs:
        _reset_mcp_state()
        return []

    _reset_mcp_state()

    async def _discover_one(server_config: dict[str, Any]) -> list[dict[str, Any]]:
        async def _handler(session: ClientSession) -> list[dict[str, Any]]:
            tools = await session.list_tools()
            return [normalize_mcp_tool_definition(tool) for tool in tools.tools]

        return await _run_mcp_session(server_config, _handler)

    try:
        definitions: list[dict[str, Any]] = []
        active_configs: list[dict[str, Any]] = []
        for server_config in server_configs:
            try:
                discovered = asyncio.run(_discover_one(server_config))
                definitions.extend(discovered)
                active_configs.append(server_config)
                for item in discovered:
                    tools_module.ACTIVE_MCP_TOOL_SERVER_BY_NAME[item["function"]["name"]] = server_config
            except Exception:
                logger.exception(t("mcp_discover_failed") + f", server={server_config.get('name')}")
                console.print(
                    Panel.fit(
                        t("mcp_tool_unavailable", name=server_config.get("name") or str(server_config)),
                        title=t("mcp_discover_failed"),
                    )
                )

        tools_module.ACTIVE_MCP_TOOL_DEFINITIONS = definitions
        tools_module.ACTIVE_MCP_TOOL_CONFIGS = active_configs
        tools_module.ACTIVE_MCP_TOOL_CONFIG = active_configs[0] if active_configs else {}
        logger.info(
            "Discovered MCP tool definitions: %s",
            [item["function"]["name"] for item in definitions],
        )
        return definitions
    except Exception:
        logger.exception(t("mcp_discover_failed"))
        _reset_mcp_state()
        return []


def run_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_config = (
        tools_module.ACTIVE_MCP_TOOL_SERVER_BY_NAME.get(name)
        or (tools_module.ACTIVE_MCP_TOOL_CONFIGS[0] if tools_module.ACTIVE_MCP_TOOL_CONFIGS else {})
        or tools_module.ACTIVE_MCP_TOOL_CONFIG
    )
    if not server_config:
        return {"status": "error", "content": t("mcp_tool_not_found", name=name)}

    async def _invoke() -> dict[str, Any]:
        async def _handler(session: ClientSession) -> dict[str, Any]:
            result = await session.call_tool(name, arguments or {})
            serialized = result.model_dump(mode="json")
            content = serialized.get("content")
            if isinstance(content, list):
                items = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if text is not None:
                            items.append(text)
                        else:
                            items.append(item)
                    else:
                        items.append(str(item))
                normalized = items[0] if len(items) == 1 else items
            else:
                normalized = content
            return {
                "status": "error" if bool(serialized.get("isError")) else "ok",
                "content": normalized,
            }

        return await _run_mcp_session(server_config, _handler)

    try:
        return asyncio.run(_invoke())
    except Exception:
        logger.exception(t("mcp_tool_failed") + f", tool={name}")
        return {"status": "error", "content": traceback.format_exc()}
