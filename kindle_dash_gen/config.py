from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from typing import Any

import yaml
from apscheduler.triggers.cron import CronTrigger

from .codex_token import normalize_token
from .text import parse_symbol_spec


DEFAULT_CONFIG: dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 5678},
    "output": {"path": "dash.png", "orientation": "portrait"},
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
        "denoise_symbols": [],
    },
    "weather": {"location": "Shanghai", "latitude": None, "longitude": None},
    "codex": {
        "token": "",
        "usage_url": "https://chatgpt.com/backend-api/wham/usage",
        "timeout_seconds": 15,
    },
}


_CONFIG_WRITE_LOCK = threading.Lock()


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    return result


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _optional_float(value: object, field: str, minimum: float, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


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


def validate_config(value: object) -> dict[str, Any]:
    """Validate and normalize the complete runtime configuration."""
    if not isinstance(value, dict):
        raise ValueError("Config must be a JSON object")

    config = deep_merge(DEFAULT_CONFIG, value)
    for section in DEFAULT_CONFIG:
        if not isinstance(config.get(section), dict):
            raise ValueError(f"{section} config must contain a mapping")

    server = config["server"]
    server["host"] = _string(server.get("host"), "server.host")
    server["port"] = _integer(server.get("port"), "server.port", 1, 65_535)

    output = config["output"]
    output["path"] = _string(output.get("path"), "output.path")
    output["orientation"] = _string(output.get("orientation"), "output.orientation").lower()
    if output["orientation"] not in {"portrait", "landscape"}:
        raise ValueError("output.orientation must be portrait or landscape")

    cache = config["cache"]
    cache["data_path"] = _string(cache.get("data_path"), "cache.data_path")

    schedule = config["schedule"]
    if not isinstance(schedule.get("enabled"), bool):
        raise ValueError("schedule.enabled must be a boolean")
    schedule["cron"] = _string(schedule.get("cron"), "schedule.cron")
    if len(schedule["cron"].split()) != 5:
        raise ValueError("schedule.cron must contain five fields")
    schedule["timezone"] = _string(schedule.get("timezone"), "schedule.timezone")
    try:
        CronTrigger.from_crontab(schedule["cron"], timezone=schedule["timezone"])
    except Exception as exc:
        raise ValueError(f"Invalid schedule: {exc}") from exc

    market = config["market"]
    symbols = market.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("market.symbols must be a list")
    normalized_symbols: list[str] = []
    for index, symbol in enumerate(symbols):
        normalized = _string(symbol, f"market.symbols[{index}]")
        primary, fallback = parse_symbol_spec(normalized)
        if "(" in normalized or ")" in normalized:
            if not primary or fallback is None:
                raise ValueError(
                    f"market.symbols[{index}] must look like PRIMARY(FALLBACK)"
                )
        canonical = f"{primary}({fallback})" if fallback else primary
        if canonical not in normalized_symbols:
            normalized_symbols.append(canonical)
    if len(normalized_symbols) > 16:
        raise ValueError("market.symbols supports at most 16 symbols")
    market["symbols"] = normalized_symbols

    denoise = market.get("denoise_symbols", [])
    if not isinstance(denoise, list):
        raise ValueError("market.denoise_symbols must be a list")
    normalized_denoise: list[str] = []
    for index, symbol in enumerate(denoise):
        normalized = _string(symbol, f"market.denoise_symbols[{index}]")
        if normalized not in normalized_denoise:
            normalized_denoise.append(normalized)
    market["denoise_symbols"] = normalized_denoise

    weather = config["weather"]
    weather["location"] = _string(weather.get("location"), "weather.location", allow_empty=True)
    weather["latitude"] = _optional_float(weather.get("latitude"), "weather.latitude", -90, 90)
    weather["longitude"] = _optional_float(weather.get("longitude"), "weather.longitude", -180, 180)
    if (weather["latitude"] is None) != (weather["longitude"] is None):
        raise ValueError("weather.latitude and weather.longitude must be set together")

    codex = config["codex"]
    codex["token"] = normalize_token(codex.get("token"))
    codex["usage_url"] = _string(codex.get("usage_url"), "codex.usage_url")
    if not codex["usage_url"].startswith(("http://", "https://")):
        raise ValueError("codex.usage_url must be an HTTP or HTTPS URL")
    codex["timeout_seconds"] = _integer(codex.get("timeout_seconds"), "codex.timeout_seconds", 1, 120)
    return config


def _atomic_write_config(config_path: Path, config: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
            delete=False,
        ) as f:
            temp_name = f.name
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, config_path)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()


def save_config(path: str | Path, value: object) -> dict[str, Any]:
    """Validate and atomically replace the complete runtime YAML file."""
    normalized = validate_config(value)
    with _CONFIG_WRITE_LOCK:
        _atomic_write_config(Path(path), normalized)
    return normalized


def save_codex_token(path: str | Path, token: object) -> None:
    """Update only codex.token and atomically replace the runtime YAML file."""
    config_path = Path(path)
    normalized = normalize_token(token)

    with _CONFIG_WRITE_LOCK:
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Config file must contain a mapping: {config_path}")
        else:
            loaded = {}

        codex = loaded.get("codex")
        if codex is None:
            codex = {}
            loaded["codex"] = codex
        if not isinstance(codex, dict):
            raise ValueError("codex config must contain a mapping")
        codex["token"] = normalized

        _atomic_write_config(config_path, loaded)
