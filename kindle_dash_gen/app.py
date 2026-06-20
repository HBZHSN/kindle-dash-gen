from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, Response, abort, send_file

from .config import load_config
from .data import CodexUsage, MarketQuote, TodoSummary, WeatherReport, fetch_codex_usage, fetch_market_quotes, fetch_weather, read_todos
from .render import DashboardData, render_dashboard


logger = logging.getLogger(__name__)


def _load_snapshot(path: Path) -> DashboardData | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return DashboardData(
            generated_at=datetime.fromisoformat(raw["generated_at"]),
            market=[MarketQuote(**item) for item in raw.get("market", [])],
            weather=WeatherReport(**raw["weather"]),
            codex=CodexUsage(**raw["codex"]),
            todos=TodoSummary(**raw["todos"]),
        )
    except Exception:
        return None


def _write_snapshot(path: Path, data: DashboardData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": data.generated_at.isoformat(),
        "market": [asdict(item) for item in data.market],
        "weather": asdict(data.weather),
        "codex": asdict(data.codex),
        "todos": asdict(data.todos),
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)
    logger.info("Snapshot written: path=%s", path)


def _merge_market(current: list[MarketQuote], previous: list[MarketQuote]) -> list[MarketQuote]:
    if not current and previous:
        return previous

    previous_by_symbol = {item.symbol: item for item in previous if item.status == "OK"}
    merged: list[MarketQuote] = []
    for item in current:
        cached = previous_by_symbol.get(item.symbol)
        merged.append(cached if item.status != "OK" and cached else item)
    return merged


def _merge_with_snapshot(current: DashboardData, previous: DashboardData | None) -> DashboardData:
    if previous is None:
        return current

    return DashboardData(
        generated_at=current.generated_at,
        market=_merge_market(current.market, previous.market),
        weather=previous.weather if current.weather.status != "OK" and previous.weather.status == "OK" else current.weather,
        codex=previous.codex if current.codex.status not in {"OK", "Skipped"} and previous.codex.status == "OK" else current.codex,
        todos=previous.todos if current.todos.status not in {"OK", "No vault"} and previous.todos.status == "OK" else current.todos,
    )


def build_dashboard(config_path: str | Path = "config.yaml") -> Path:
    config = load_config(config_path)
    output_path = Path(config.get("output", {}).get("path") or "dash.png")
    snapshot_path = Path(config.get("cache", {}).get("data_path") or ".cache/dashboard-data.json")
    previous = _load_snapshot(snapshot_path)
    logger.info("Dashboard build started: config=%s output=%s snapshot=%s", config_path, output_path, snapshot_path)
    current = DashboardData(
        generated_at=datetime.now().astimezone(),
        market=fetch_market_quotes(list(config.get("market", {}).get("symbols") or [])),
        weather=fetch_weather(config.get("weather", {})),
        codex=fetch_codex_usage(config.get("codex", {})),
        todos=read_todos(config.get("obsidian", {})),
    )
    data = _merge_with_snapshot(current, previous)
    _write_snapshot(snapshot_path, data)
    orientation = str(config.get("output", {}).get("orientation") or "portrait")
    rendered = render_dashboard(data, output_path, orientation).resolve()
    logger.info("Dashboard rendered: path=%s", rendered)
    return rendered


def dashboard_output_path(config_path: str | Path = "config.yaml") -> Path:
    config = load_config(config_path)
    return Path(config.get("output", {}).get("path") or "dash.png").resolve()


def create_app(config_path: str | Path = "config.yaml") -> Flask:
    app = Flask(__name__)
    app.config["DASH_CONFIG_PATH"] = str(config_path)

    @app.get("/")
    def index() -> Response:
        return Response("OK. Fetch /dash.png\n", mimetype="text/plain")

    @app.get("/dash.png")
    def dash_png():
        path = dashboard_output_path(app.config["DASH_CONFIG_PATH"])
        if not path.exists():
            abort(503, "dash.png has not been generated yet")
        return send_file(path, mimetype="image/png", max_age=0)

    return app


def start_scheduler(config_path: str | Path = "config.yaml") -> BackgroundScheduler | None:
    config = load_config(config_path)
    schedule: dict[str, Any] = config.get("schedule", {})
    if not schedule.get("enabled", True):
        return None

    scheduler = BackgroundScheduler(timezone=schedule.get("timezone") or "Asia/Shanghai")
    trigger = CronTrigger.from_crontab(schedule.get("cron") or "*/15 * * * *", timezone=scheduler.timezone)
    scheduler.add_job(
        lambda: build_dashboard(config_path),
        trigger=trigger,
        id="render_dash",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started: cron=%s timezone=%s", schedule.get("cron") or "*/15 * * * *", scheduler.timezone)
    return scheduler
