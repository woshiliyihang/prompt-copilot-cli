from __future__ import annotations
from config import ACTIVE_MCP_TOOL_CONFIG
from config import ACTIVE_MCP_TOOL_CONFIGS
from config import ACTIVE_MCP_TOOL_SERVER_BY_NAME
from config import console
from config import logger
from config import t
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from rich.panel import Panel
from typing import Any
import asyncio
import traceback

ACTIVE_MCP_TOOL_DEFINITIONS: list[dict[str, Any]] = []

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
    args = list(server_config.get("args") or ["-y", "bing-cn-mcp"])
    server_params = StdioServerParameters(command=command, args=args)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await handler(session)

def normalize_mcp_server_config(raw_config: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(fallback or {})
    if isinstance(raw_config, dict):
        merged = dict(base)
        merged.update(raw_config)
        transport = str(merged.get("transport") or merged.get("type") or base.get("transport") or "stdio").lower()
        if transport in {"sse", "http", "streamable_http", "streamable-http"}:
            headers = merged.get("headers") or base.get("headers") or {}
            if isinstance(headers, dict):
                headers = dict(headers)
            else:
                headers = {}
            url = merged.get("url") or base.get("url") or ""
            return {
                "name": merged.get("name") or url or f"mcp-{transport}",
                "transport": transport,
                "url": url,
                "headers": headers,
            }

        command = merged.get("command") or base.get("command") or "npx"
        args_value = merged.get("args") or merged.get("arguments") or base.get("args") or ["-y", "bing-cn-mcp"]
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

    return {"name": "mcp", "transport": "stdio", "command": "npx", "args": ["-y", "bing-cn-mcp"]}

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
            return [normalize_mcp_server_config(item, fallback=fallback) for item in mcp_cfg["servers"] if item is not None]
        return [normalize_mcp_server_config(mcp_cfg)]

    return []

def discover_mcp_tools(model_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    global ACTIVE_MCP_TOOL_DEFINITIONS, ACTIVE_MCP_TOOL_CONFIG, ACTIVE_MCP_TOOL_CONFIGS, ACTIVE_MCP_TOOL_SERVER_BY_NAME

    mcp_cfg = model_cfg.get("mcp", {})
    if isinstance(mcp_cfg, dict):
        enabled = bool(mcp_cfg.get("enabled", True))
    else:
        enabled = True
    if not enabled:
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []

    server_configs = normalize_mcp_server_configs(mcp_cfg)
    if not server_configs:
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []

    ACTIVE_MCP_TOOL_CONFIGS = []
    ACTIVE_MCP_TOOL_CONFIG = {}
    ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
    ACTIVE_MCP_TOOL_DEFINITIONS = []

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
                    ACTIVE_MCP_TOOL_SERVER_BY_NAME[item["function"]["name"]] = server_config
            except Exception:
                logger.exception(t("mcp_discover_failed") + f", server={server_config.get('name')}")
                console.print(Panel.fit(t("mcp_tool_unavailable", name=server_config.get("name") or str(server_config)), title=t("mcp_discover_failed")))

        ACTIVE_MCP_TOOL_DEFINITIONS = definitions
        ACTIVE_MCP_TOOL_CONFIGS = active_configs
        ACTIVE_MCP_TOOL_CONFIG = active_configs[0] if active_configs else {}
        logger.info("Discovered MCP tool definitions: %s", [item["function"]["name"] for item in definitions])
        return definitions
    except Exception:
        logger.exception(t("mcp_discover_failed"))
        ACTIVE_MCP_TOOL_DEFINITIONS = []
        ACTIVE_MCP_TOOL_CONFIG = {}
        ACTIVE_MCP_TOOL_CONFIGS = []
        ACTIVE_MCP_TOOL_SERVER_BY_NAME = {}
        return []

def run_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server_config = ACTIVE_MCP_TOOL_SERVER_BY_NAME.get(name) or ACTIVE_MCP_TOOL_CONFIGS[0] or ACTIVE_MCP_TOOL_CONFIG
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
