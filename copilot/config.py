"""Configuration loading, validation and OpenAI client construction."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from . import globals_
from .i18n import t
from .prompts import build_system_prompt

CONFIG_SAVE_FILE_PATH = globals_.ROOT / "config.json"

DEFAULT_MODEL_CONFIG: dict[str, Any] = {
    "model": "",
    "base_url": "",
    "api_key": "",
    "temperature": 0.2,
    "debug": False,
    "memory": {
        "enabled": True,
        "auto_extract": True,
        "max_results": 3,
    },
    "mcp": {
        "enabled": True,
        "servers": [
            # {
            #     "name": "bing",
            #     "command": "npx",
            #     "args": ["-y", "bing-cn-mcp"]
            # },
            # {
            #     "name": "open-websearch-http",
            #     "transport": "http",
            #     "url": "http://127.0.0.1:3000/mcp"
            # }
        ],
    },
}

REQUIRED_FIELDS = ["model", "base_url", "api_key"]


def config_field_descriptions() -> dict[str, str]:
    return {
        "model": t("config_field_model"),
        "base_url": t("config_field_base_url"),
        "api_key": t("config_field_api_key"),
        "temperature": t("config_field_temperature"),
        "debug": t("config_field_debug"),
        "memory": t("config_field_memory"),
        "mcp": t("config_field_mcp"),
    }


def _format_config_field_help() -> str:
    lines = [t("config_header")]
    for field_name, description in config_field_descriptions().items():
        lines.append(f"- {field_name}: {description}")
    return "\n".join(lines)


def _merge_defaults(file_payload: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *file_payload* over a fresh copy of the defaults."""
    merged: dict[str, Any] = {}
    for key, value in DEFAULT_MODEL_CONFIG.items():
        if isinstance(value, dict):
            merged[key] = dict(value)
        else:
            merged[key] = value
    for key, value in file_payload.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def ensure_config(workdir: Path, language: str) -> tuple[dict[str, Any], str]:
    """Load (creating if needed) the config file and build the system prompt.

    Returns ``(model_config, system_prompt)``.  Raises ``RuntimeError`` with a
    localized, actionable message when the configuration is missing or invalid.
    """
    CONFIG_SAVE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_SAVE_FILE_PATH.exists():
        default_payload = {
            key: (value.copy() if isinstance(value, dict) else value)
            for key, value in DEFAULT_MODEL_CONFIG.items()
        }
        CONFIG_SAVE_FILE_PATH.write_text(
            json.dumps(default_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    try:
        file_payload = json.loads(CONFIG_SAVE_FILE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            t("config_file_read_error", path=CONFIG_SAVE_FILE_PATH, error=exc) + "\n\n"
            + _format_config_field_help()
        ) from exc

    if not isinstance(file_payload, dict):
        raise RuntimeError(
            t("config_file_format_error", path=CONFIG_SAVE_FILE_PATH) + "\n\n"
            + _format_config_field_help()
        )

    model_cfg = _merge_defaults(file_payload)

    missing_fields = [field for field in REQUIRED_FIELDS if not str(model_cfg.get(field, "")).strip()]
    if missing_fields:
        raise RuntimeError(
            t("config_incomplete", path=CONFIG_SAVE_FILE_PATH, fields=", ".join(missing_fields)) + "\n\n"
            + _format_config_field_help()
        )

    system_prompt = build_system_prompt(str(workdir), language)
    return model_cfg, system_prompt


def build_client(model_cfg: dict[str, Any]) -> OpenAI:
    api_key = model_cfg.get("api_key")
    if not api_key:
        raise RuntimeError(t("config_api_key_error"))
    base_url = model_cfg.get("base_url")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    kwargs["timeout"] = globals_.MODEL_REQUEST_TIMEOUT_SECONDS
    return OpenAI(**kwargs)


def memory_settings(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the effective memory configuration with defaults applied."""
    raw = model_cfg.get("memory")
    defaults = dict(DEFAULT_MODEL_CONFIG["memory"])
    if isinstance(raw, dict):
        defaults.update(raw)
    defaults["enabled"] = bool(defaults.get("enabled", True))
    defaults["auto_extract"] = bool(defaults.get("auto_extract", True))
    try:
        defaults["max_results"] = max(0, int(defaults.get("max_results", 3)))
    except (TypeError, ValueError):
        defaults["max_results"] = 3
    return defaults
