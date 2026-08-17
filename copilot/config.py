from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".prompt-copilot"
CONFIG_PATH = ROOT / "config.json"
MEMORY_DB_PATH = ROOT / "memory.db"

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "temperature": 0.2,
    "timeout": 120,
    "memory": {"enabled": True, "max_recent_memories": 5},
    "context": {"summary_trigger_tokens": 12000, "keep_messages": 20},
}


def _merge(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    ROOT.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"无法读取配置文件 {CONFIG_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"配置文件必须是 JSON 对象: {CONFIG_PATH}")
    config = _merge(DEFAULT_CONFIG, payload)
    for field in ("model", "base_url", "api_key"):
        if not str(config.get(field, "")).strip():
            raise RuntimeError(f"配置缺少 {field}，请编辑 {CONFIG_PATH}")
    return config
