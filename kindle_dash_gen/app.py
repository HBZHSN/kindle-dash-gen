from __future__ import annotations

import io
import json
import logging
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image

from .codex_token import inspect_token, normalize_token
from .config import load_config, save_codex_token, save_config
from .data import (
    CODEX_NOT_STARTED_REFRESH_SECONDS,
    CodexUsage,
    MarketQuote,
    WeatherReport,
    fetch_codex_usage,
    fetch_market_quotes,
    fetch_weather,
    should_refresh_codex,
)
from .logbuffer import install_log_buffer
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
        if item.status != "OK" and cached:
            merged.append(cached)
        elif cached and not item.intraday and cached.intraday:
            merged.append(replace(item, intraday=cached.intraday))
        else:
            merged.append(item)
    return merged


def _merge_with_snapshot(current: DashboardData, previous: DashboardData | None) -> DashboardData:
    if previous is None:
        return current

    codex = current.codex
    if current.codex.status not in {"OK", "Skipped"} and previous.codex.status == "OK":
        codex = replace(
            previous.codex,
            token_expires_at=current.codex.token_expires_at,
            token_expiring_soon=current.codex.token_expiring_soon,
            token_expired=current.codex.token_expired,
        )

    return DashboardData(
        generated_at=current.generated_at,
        market=_merge_market(current.market, previous.market),
        weather=previous.weather if current.weather.status != "OK" and previous.weather.status == "OK" else current.weather,
        codex=codex,
    )


def _collect_dashboard_data(config: dict[str, Any], previous: DashboardData | None) -> DashboardData:
    previous_codex = previous.codex if previous else None
    throttle_seconds = int(
        config.get("codex", {}).get("not_started_refresh_seconds")
        or CODEX_NOT_STARTED_REFRESH_SECONDS
    )
    if should_refresh_codex(previous_codex, throttle_seconds):
        codex = fetch_codex_usage(config.get("codex", {}))
    else:
        codex = previous_codex
        logger.info("Codex usage throttled: 5H window not started, reusing cached usage")
    current = DashboardData(
        generated_at=datetime.now().astimezone(),
        market=fetch_market_quotes(
            list(config.get("market", {}).get("symbols") or []),
            list(config.get("market", {}).get("denoise_symbols") or []),
        ),
        weather=fetch_weather(config.get("weather", {})),
        codex=codex,
    )
    return _merge_with_snapshot(current, previous)


def build_dashboard(config_path: str | Path = "config.yaml") -> Path:
    config = load_config(config_path)
    output_path = Path(config.get("output", {}).get("path") or "dash.png")
    snapshot_path = Path(config.get("cache", {}).get("data_path") or ".cache/dashboard-data.json")
    previous = _load_snapshot(snapshot_path)
    logger.info("Dashboard build started: config=%s output=%s snapshot=%s", config_path, output_path, snapshot_path)
    data = _collect_dashboard_data(config, previous)
    _write_snapshot(snapshot_path, data)
    orientation = str(config.get("output", {}).get("orientation") or "portrait")
    rendered = render_dashboard(data, output_path, orientation).resolve()
    logger.info("Dashboard rendered: path=%s", rendered)
    return rendered


def dashboard_output_path(config_path: str | Path = "config.yaml") -> Path:
    config = load_config(config_path)
    return Path(config.get("output", {}).get("path") or "dash.png").resolve()


def _browser_preview(path: Path, orientation: str) -> io.BytesIO:
    """Return a browser-oriented PNG without changing the Kindle output file."""
    with Image.open(path) as source:
        image = source.convert("L")
        if orientation == "landscape":
            image = image.transpose(Image.Transpose.ROTATE_90)
        result = io.BytesIO()
        image.save(result, format="PNG")
    result.seek(0)
    return result


def create_app(config_path: str | Path = "config.yaml") -> Flask:
    app = Flask(__name__)
    app.config["DASH_CONFIG_PATH"] = str(config_path)
    log_buffer = install_log_buffer()

    @app.after_request
    def disable_settings_cache(response):
        if request.path == "/" or request.path == "/settings" or request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.get("/")
    @app.get("/settings")
    def index():
        return render_template("settings.html")

    @app.get("/healthz")
    def healthz():
        return "OK\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.get("/api/config")
    def get_config():
        try:
            config = load_config(app.config["DASH_CONFIG_PATH"])
            token = normalize_token(config.get("codex", {}).get("token"))
            return jsonify({"config": config, "token_info": inspect_token(token)})
        except (OSError, ValueError) as exc:
            logger.warning("Could not read config: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.put("/api/config")
    def update_config():
        if request.content_length and request.content_length > 100_000:
            return jsonify({"error": "Request is too large"}), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON config object is required"}), 400
        try:
            config = save_config(app.config["DASH_CONFIG_PATH"], payload.get("config", payload))
            token = config.get("codex", {}).get("token", "")
            logger.info("Runtime config updated")
            return jsonify({"config": config, "token_info": inspect_token(token)})
        except ValueError as exc:
            logger.warning("Could not update config: %s", exc)
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            logger.exception("Could not write config")
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/codex-token")
    def get_codex_token():
        try:
            config = load_config(app.config["DASH_CONFIG_PATH"])
            token = normalize_token(config.get("codex", {}).get("token"))
            return jsonify({"token": token, **inspect_token(token)})
        except (OSError, ValueError) as exc:
            logger.warning("Could not read Codex token config: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.put("/api/codex-token")
    def update_codex_token():
        if request.content_length and request.content_length > 20_000:
            return jsonify({"error": "Request is too large"}), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "token" not in payload:
            return jsonify({"error": "JSON field 'token' is required"}), 400
        try:
            token = normalize_token(payload["token"])
            save_codex_token(app.config["DASH_CONFIG_PATH"], token)
            logger.info("Codex token config updated")
            return jsonify({"token": token, **inspect_token(token)})
        except ValueError as exc:
            logger.warning("Could not update Codex token config: %s", exc)
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            logger.exception("Could not write Codex token config")
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/preview")
    def current_preview():
        try:
            config = load_config(app.config["DASH_CONFIG_PATH"])
            path = Path(config.get("output", {}).get("path") or "dash.png").resolve()
            if not path.exists():
                return jsonify({"error": "dash.png has not been generated yet"}), 503
            orientation = str(config.get("output", {}).get("orientation") or "portrait")
            return send_file(_browser_preview(path, orientation), mimetype="image/png", max_age=0)
        except (OSError, ValueError) as exc:
            logger.warning("Could not create current preview: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/logs")
    def get_logs():
        after_raw = request.args.get("after")
        limit_raw = request.args.get("limit")
        try:
            after = int(after_raw) if after_raw not in (None, "") else None
            limit = int(limit_raw) if limit_raw not in (None, "") else None
        except ValueError:
            return jsonify({"error": "'after' and 'limit' must be integers"}), 400
        return jsonify(log_buffer.snapshot(after=after, limit=limit))

    @app.delete("/api/logs")
    def clear_logs():
        log_buffer.clear()
        logger.info("Log buffer cleared from settings page")
        return jsonify(log_buffer.snapshot())

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
