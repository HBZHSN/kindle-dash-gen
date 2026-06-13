from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 5678},
    "output": {"path": "dash.png"},
    "cache": {"data_path": ".cache/dashboard-data.json"},
    "schedule": {
        "enabled": True,
        "cron": "*/15 * * * *",
        "timezone": "Asia/Shanghai",
    },
    "market": {
        "symbols": [
            "600519.SS",
            "000001.SZ",
            "AAPL",
            "MSFT",
            "BTC-USD",
            "ETH-USD",
            "^NDX",
            "GC=F",
        ],
    },
    "weather": {"location": "Shanghai", "latitude": None, "longitude": None},
    "obsidian": {
        "path": "",
        "projects_dir": "1-Projects",
        "archive_dir": "4-Archive",
        "max_items": 8,
    },
    "codex": {
        "token": "",
        "usage_url": "https://chatgpt.com/backend-api/wham/usage",
        "timeout_seconds": 15,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return deepcopy(DEFAULT_CONFIG)

    with config_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {config_path}")

    return deep_merge(DEFAULT_CONFIG, loaded)
