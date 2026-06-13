from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

from .text import ascii_text


logger = logging.getLogger(__name__)


@dataclass
class MarketQuote:
    symbol: str
    price: str
    change: str
    status: str = "OK"


@dataclass
class WeatherReport:
    title: str
    temperature: str
    wind: str
    status: str = "OK"


@dataclass
class CodexUsage:
    primary: str
    secondary: str
    allowed: str
    status: str = "OK"


@dataclass
class TodoSummary:
    open_items: list[str]
    done_items: list[str]
    status: str = "OK"


@contextmanager
def _quiet_yfinance_logs():
    logger = logging.getLogger("yfinance")
    old_level = logger.level
    old_propagate = logger.propagate
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False
    try:
        yield
    finally:
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def _close_prices(data: Any, symbol: str) -> Any:
    if "Close" in data.columns:
        return data["Close"].dropna()
    if (symbol, "Close") in data.columns:
        return data[(symbol, "Close")].dropna()
    if ("Close", symbol) in data.columns:
        return data[("Close", symbol)].dropna()
    raise ValueError("no close prices")


def _fetch_market_quote(symbol: str) -> MarketQuote:
    try:
        with _quiet_yfinance_logs():
            data = yf.download(
                tickers=symbol,
                period="2d",
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=False,
                auto_adjust=False,
                timeout=10,
            )
        if data is None or data.empty:
            raise ValueError("no market data")
        closes = _close_prices(data, symbol)
        if closes.empty:
            raise ValueError("no close prices")
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        pct = 0.0 if prev == 0 else ((last - prev) / prev) * 100
        quote = MarketQuote(
            symbol=ascii_text(symbol, "SYM"),
            price=f"{last:,.2f}",
            change=f"{pct:+.2f}%",
        )
        logger.info("Market quote fetched: symbol=%s price=%s change=%s", quote.symbol, quote.price, quote.change)
        return quote
    except Exception as exc:
        quote = MarketQuote(symbol=ascii_text(symbol, "SYM"), price="--", change="--", status=ascii_text(exc, "Failed"))
        logger.warning("Market quote failed: symbol=%s error=%s", quote.symbol, quote.status)
        return quote


def fetch_market_quotes(symbols: list[str]) -> list[MarketQuote]:
    if not symbols:
        return []

    try:
        cache_dir = Path(".cache") / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass

    quotes: list[MarketQuote] = []
    for symbol in symbols:
        quotes.append(_fetch_market_quote(symbol))
    logger.info(
        "Market quotes complete: total=%d ok=%d failed=%d",
        len(quotes),
        sum(1 for quote in quotes if quote.status == "OK"),
        sum(1 for quote in quotes if quote.status != "OK"),
    )
    return quotes


def fetch_weather(config: dict[str, Any]) -> WeatherReport:
    location = ascii_text(config.get("location"), "Weather")
    latitude = config.get("latitude")
    longitude = config.get("longitude")

    try:
        if latitude is None or longitude is None:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": config.get("location") or "Shanghai", "count": 1, "language": "en", "format": "json"},
                timeout=10,
            )
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                raise ValueError("location not found")
            first = results[0]
            latitude = first["latitude"]
            longitude = first["longitude"]
            location = ascii_text(first.get("name"), location)

        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,wind_speed_10m",
                "timezone": "auto",
            },
            timeout=10,
        )
        weather.raise_for_status()
        current = weather.json().get("current") or {}
        temp = current.get("temperature_2m")
        wind = current.get("wind_speed_10m")
        report = WeatherReport(
            title=location,
            temperature=f"{float(temp):.1f} C" if temp is not None else "-- C",
            wind=f"Wind {float(wind):.1f} km/h" if wind is not None else "Wind --",
        )
        logger.info(
            "Weather fetched: title=%s temperature=%s wind=%s",
            report.title,
            report.temperature,
            report.wind,
        )
        return report
    except Exception as exc:
        report = WeatherReport(title=location, temperature="-- C", wind="Wind --", status=ascii_text(exc, "Failed"))
        logger.warning("Weather failed: title=%s error=%s", report.title, report.status)
        return report


def _format_window(window: dict[str, Any] | None) -> str:
    if not window:
        return "N/A"

    used = window.get("used_percent")
    limit_seconds = window.get("limit_window_seconds")
    reset_seconds = window.get("reset_after_seconds")
    if used == 1 and limit_seconds and reset_seconds and int(limit_seconds) == int(reset_seconds):
        used = 0
        note = "not started"
    else:
        note = "used"

    reset_at = window.get("reset_at")
    if reset_at:
        reset = datetime.fromtimestamp(int(reset_at), tz=timezone.utc).astimezone().strftime("%m-%d %H:%M")
        return f"{used}% {note}, reset {reset}"
    return f"{used}% {note}"


def fetch_codex_usage(config: dict[str, Any]) -> CodexUsage:
    token = config.get("token") or ""
    if not token:
        logger.info("Codex usage skipped: no token configured")
        return CodexUsage(primary="No token", secondary="N/A", allowed="N/A", status="Skipped")

    try:
        response = requests.get(
            config.get("usage_url") or "https://chatgpt.com/backend-api/wham/usage",
            headers={
                "accept": "*/*",
                "authorization": f"Bearer {token}",
                "x-openai-target-path": "/backend-api/wham/usage",
                "x-openai-target-route": "/backend-api/wham/usage",
            },
            timeout=int(config.get("timeout_seconds") or 15),
        )
        response.raise_for_status()
        payload = response.json()
        rate_limit = payload.get("rate_limit") or {}
        usage = CodexUsage(
            primary=_format_window(rate_limit.get("primary_window")),
            secondary=_format_window(rate_limit.get("secondary_window")),
            allowed="yes" if rate_limit.get("allowed") else "no",
        )
        logger.info(
            "Codex usage fetched: primary=%s secondary=%s allowed=%s",
            usage.primary,
            usage.secondary,
            usage.allowed,
        )
        return usage
    except Exception as exc:
        usage = CodexUsage(primary="Unavailable", secondary="Unavailable", allowed="N/A", status=ascii_text(exc, "Failed"))
        logger.warning("Codex usage failed: error=%s", usage.status)
        return usage


_DATE_PATTERNS = [
    re.compile(r"^\s*(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(?=\D|$)"),
    re.compile(r"^\s*(?P<year>\d{4})[-_ .](?P<month>\d{1,2})[-_ .](?P<day>\d{1,2})(?=\D|$)"),
    re.compile(r"^\s*(?P<year>\d{2})(?P<month>\d{2})(?P<day>\d{2})(?=\D|$)"),
    re.compile(r"^\s*(?P<month>\d{1,2})[-_ .](?P<day>\d{1,2})(?=\D|$)"),
]


def _clean_task_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r"^\s*[0-9]{4}[-_ .]?[0-9]{1,2}[-_ .]?[0-9]{1,2}[-_ .]*", "", name)
    cleaned = re.sub(r"^\s*[0-9]{6,8}[-_ .]*", "", cleaned)
    cleaned = re.sub(r"^\s*[0-9]{1,2}[-_ .][0-9]{1,2}[-_ .]*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def _task_date_from_name(name: str, fallback: date) -> date:
    for pattern in _DATE_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        try:
            year_text = match.groupdict().get("year")
            year = int(year_text) if year_text else fallback.year
            if year < 100:
                year += 2000
            return date(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            continue
    return fallback


def _task_label(task_date: date, task_name: str) -> str:
    return f"{task_date:%m-%d}  {task_name}"


def _list_child_names(root: Path, max_items: int, fallback_prefix: str) -> list[str]:
    if not root.exists() or not root.is_dir():
        logger.info("Todo directory missing: path=%s", root)
        return []

    items: list[tuple[date, str, str]] = []
    for index, path in enumerate(root.iterdir(), start=1):
        try:
            fallback_date = datetime.fromtimestamp(path.stat().st_mtime).date()
        except OSError:
            fallback_date = datetime.now().date()
        raw_name = path.stem if path.is_file() else path.name
        task_date = _task_date_from_name(raw_name, fallback_date)
        task_name = _clean_task_name(raw_name, f"{fallback_prefix} {index}")
        items.append((task_date, task_name, raw_name.lower()))

    items.sort(key=lambda item: (item[0], item[2]), reverse=True)
    labels = [_task_label(task_date, task_name) for task_date, task_name, _ in items[:max_items]]
    logger.info("Todo directory read: path=%s total=%d selected=%d", root, len(items), len(labels))
    return labels


def read_todos(config: dict[str, Any]) -> TodoSummary:
    vault_raw = config.get("path") or ""
    if not vault_raw:
        return TodoSummary(open_items=[], done_items=[], status="No vault")

    vault = Path(vault_raw)
    max_items = int(config.get("max_items") or 8)
    projects = vault / str(config.get("projects_dir") or "1-Projects")
    archive = vault / str(config.get("archive_dir") or "4-Archive")
    try:
        summary = TodoSummary(
            open_items=_list_child_names(projects, max_items, "Task"),
            done_items=_list_child_names(archive, max_items, "Done"),
        )
        logger.info(
            "Todos read: open=%d done=%d open_items=%s done_items=%s",
            len(summary.open_items),
            len(summary.done_items),
            summary.open_items,
            summary.done_items,
        )
        return summary
    except Exception as exc:
        summary = TodoSummary(open_items=[], done_items=[], status=ascii_text(exc, "Failed"))
        logger.warning("Todos failed: error=%s", summary.status)
        return summary
