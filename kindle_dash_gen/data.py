from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, time as dtime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from .codex_token import inspect_token, normalize_token
from .text import ascii_text, parse_symbol_spec


logger = logging.getLogger(__name__)


@dataclass
class MarketQuote:
    symbol: str
    price: str
    change: str
    status: str = "OK"
    intraday: list[float | None] = field(default_factory=list)
    is_closed: bool = False
    trading_progress: float = 1.0
    delay_minutes: int = 0
    is_24h: bool = False
    secondary_symbol: str = ""
    secondary_price: str = ""
    secondary_change: str = ""


@dataclass
class WeatherReport:
    title: str
    temperature: str
    wind: str
    weather_code: int | None = None
    status: str = "OK"


@dataclass
class CodexUsage:
    primary: str
    secondary: str
    allowed: str
    status: str = "OK"
    primary_t: int = 0
    primary_e: int = 0
    secondary_t: int = 0
    secondary_e: int = 0
    token_expires_at: int | None = None
    token_expiring_soon: bool = False
    token_expired: bool = False
    not_started: bool = False
    fetched_at: float | None = None


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


def _positive_number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _to_utc_datetime(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _market_is_closed(metadata: dict[str, Any], now: datetime | None = None) -> bool:
    state = str(metadata.get("marketState") or "").strip().upper()
    if state in {"POST", "POSTPOST", "CLOSED"}:
        return True
    if state in {"PRE", "PREPRE", "REGULAR"}:
        return False

    regular = (metadata.get("currentTradingPeriod") or {}).get("regular") or {}
    end = regular.get("end")
    if end is None:
        return False
    try:
        end_time = _to_utc_datetime(end)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        start = regular.get("start")
        if start is not None:
            start_time = _to_utc_datetime(start)
            return not (start_time <= current_time < end_time)
        return current_time >= end_time
    except (TypeError, ValueError, OSError, OverflowError):
        return False


def _trading_progress(metadata: dict[str, Any], now: datetime | None = None) -> float:
    regular = (metadata.get("currentTradingPeriod") or {}).get("regular") or {}
    start = regular.get("start")
    end = regular.get("end")
    if start is None or end is None:
        return 1.0
    try:
        start_time = _to_utc_datetime(start)
        end_time = _to_utc_datetime(end)
    except (TypeError, ValueError, OSError, OverflowError):
        return 1.0
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    total = (end_time - start_time).total_seconds()
    if total <= 0:
        return 1.0
    elapsed = (current - start_time).total_seconds()
    return max(0.0, min(1.0, elapsed / total))


def _quote_delay_minutes(
    metadata: dict[str, Any], is_closed: bool, now: datetime | None = None
) -> int:
    if is_closed:
        return 0
    quote_time = metadata.get("regularMarketTime")
    if not isinstance(quote_time, (int, float)):
        return 0
    try:
        quoted_at = datetime.fromtimestamp(float(quote_time), timezone.utc)
    except (OverflowError, OSError, ValueError):
        return 0
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    minutes = round((current - quoted_at).total_seconds() / 60)
    return minutes if minutes >= 2 else 0


def _is_24h_market(metadata: dict[str, Any]) -> bool:
    instrument = str(metadata.get("instrumentType") or "").strip().upper()
    if instrument == "CRYPTOCURRENCY":
        return True
    regular = (metadata.get("currentTradingPeriod") or {}).get("regular") or {}
    start = regular.get("start")
    end = regular.get("end")
    if not (isinstance(start, (int, float)) and isinstance(end, (int, float))):
        return False
    return (end - start) >= 23 * 3600


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _drop_price_outliers(prices: list[float]) -> list[float]:
    """Remove glitched intraday bars that sit far off the curve.

    yfinance occasionally returns isolated one-minute closes that are wildly
    off (often a recurring bad value far below the real price). Those points
    show up as a lone dot or spike that does not connect into the curve.

    A global filter is wrong here: during a trending day the first/last bars are
    legitimately far from the day's median. Instead compare each point to the
    median of its local neighbours and drop only the ones that break away from
    that local trend, so genuine moves are kept but spikes are removed.
    """
    n = len(prices)
    if n < 7:
        return prices
    radius = 3
    residuals: list[float] = []
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n, i + radius + 1)
        window = prices[lo:i] + prices[i + 1 : hi]
        residuals.append(prices[i] - _median(window))
    scale = _median([abs(r) for r in residuals])
    if scale <= 0:
        return prices
    kept = [value for value, residual in zip(prices, residuals) if abs(residual) <= 8.0 * scale]
    if len(kept) < 2 or len(kept) < n // 2:
        return prices
    return kept


def _quote_from_intraday(
    symbol: str, closes: Any, metadata: dict[str, Any], denoise: bool = False
) -> MarketQuote:
    prices = [float(value) for value in closes.tolist() if math.isfinite(float(value))]
    if denoise:
        prices = _drop_price_outliers(prices)
    if not prices:
        raise ValueError("no intraday close prices")

    last = _positive_number(metadata.get("regularMarketPrice")) or prices[-1]
    prev = (
        _positive_number(metadata.get("previousClose"))
        or _positive_number(metadata.get("chartPreviousClose"))
        or _positive_number(metadata.get("regularMarketPreviousClose"))
        or _prev_close_from_info(symbol, last)
    )
    if prev <= 0:
        prev = last

    # Yahoo can publish an official index/futures close that differs slightly
    # from the final one-minute bar. Use it as the curve endpoint so the chart
    # and displayed quote share the exact same current value and baseline.
    prices[-1] = last
    intraday = [round(((price - prev) / prev) * 100, 4) for price in prices]
    pct = ((last - prev) / prev) * 100
    is_closed = _market_is_closed(metadata)
    is_24h = _is_24h_market(metadata)
    return MarketQuote(
        symbol=ascii_text(symbol, "SYM"),
        price=f"{last:,.2f}",
        change=f"{pct:+.2f}%",
        intraday=intraday,
        is_closed=is_closed,
        trading_progress=1.0 if is_closed else _trading_progress(metadata),
        delay_minutes=_quote_delay_minutes(metadata, is_closed),
        is_24h=is_24h,
    )


def _fetch_single_quote(symbol: str, denoise: bool = False) -> MarketQuote:
    try:
        ticker = yf.Ticker(symbol)
        with _quiet_yfinance_logs():
            data = ticker.history(
                period="1d",
                interval="1m",
                auto_adjust=False,
                actions=False,
                timeout=15,
            )
        if data is None or data.empty:
            raise ValueError("no market data")
        closes = _close_prices(data, symbol)
        quote = _quote_from_intraday(symbol, closes, ticker.history_metadata or {}, denoise)
        logger.info(
            "Market quote fetched: symbol=%s price=%s change=%s intraday_points=%d closed=%s",
            quote.symbol,
            quote.price,
            quote.change,
            len(quote.intraday),
            quote.is_closed,
        )
        return quote
    except Exception as exc:
        quote = MarketQuote(symbol=ascii_text(symbol, "SYM"), price="--", change="--", status=ascii_text(exc, "Failed"))
        logger.warning("Market quote failed: symbol=%s error=%s", quote.symbol, quote.status)
        return quote


def _custom_sessions(config: dict[str, Any]) -> list[tuple[dtime, dtime]]:
    sessions: list[tuple[dtime, dtime]] = []
    for value in config.get("sessions") or ["09:00-11:30", "13:00-15:00"]:
        start, end = str(value).split("-", 1)
        sessions.append((dtime.fromisoformat(start), dtime.fromisoformat(end)))
    return sessions


def _custom_trading_progress(
    quote_date: date,
    sessions: list[tuple[dtime, dtime]],
    now: datetime,
) -> float:
    if quote_date < now.date():
        return 1.0
    total = sum(
        (datetime.combine(quote_date, end) - datetime.combine(quote_date, start)).total_seconds()
        for start, end in sessions
    )
    if total <= 0:
        return 1.0
    elapsed = 0.0
    current = now.timetz().replace(tzinfo=None)
    for start, end in sessions:
        duration = (
            datetime.combine(quote_date, end) - datetime.combine(quote_date, start)
        ).total_seconds()
        if current >= end:
            elapsed += duration
        elif current > start:
            elapsed += (
                datetime.combine(quote_date, current) - datetime.combine(quote_date, start)
            ).total_seconds()
    return max(0.0, min(1.0, elapsed / total))


def _custom_minute_returns(
    rows: list[tuple[dict[str, Any], float]],
    quote_date: date,
    sessions: list[tuple[dtime, dtime]],
    now: datetime,
) -> list[float | None]:
    by_minute: dict[dtime, float] = {}
    for row, value in rows:
        try:
            timestamp = dtime.fromisoformat(str(row["Time"]))
        except (KeyError, TypeError, ValueError):
            continue
        minute = timestamp.replace(second=0, microsecond=0)
        if any(start <= minute < end for start, end in sessions):
            # Rows are chronological; the last observation in each minute wins.
            by_minute[minute] = round(value * 100, 4)

    current_time = now.timetz().replace(tzinfo=None)
    historical = quote_date < now.date()
    values: list[float | None] = []
    for start, end in sessions:
        cursor = datetime.combine(quote_date, start)
        session_end = datetime.combine(quote_date, end)
        while cursor < session_end:
            minute = cursor.time()
            if not historical and minute > current_time:
                break
            values.append(by_minute.get(minute))
            cursor += timedelta(minutes=1)
    return values


def _fetch_custom_quote(
    symbol: str,
    config: dict[str, Any],
    denoise: bool = False,
    now: datetime | None = None,
) -> MarketQuote:
    try:
        zone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
        current = now.astimezone(zone) if now else datetime.now(zone)
        sessions = _custom_sessions(config)
        session = requests.Session()
        session.trust_env = False
        payload: dict[str, Any] | None = None
        quote_date: date | None = None
        for days_back in range(int(config.get("lookback_days", 30)) + 1):
            candidate = current.date() - timedelta(days=days_back)
            response = session.get(
                str(config["url"]),
                params={"product": config["product"], "date": candidate.strftime("%Y%m%d")},
                timeout=int(config.get("timeout_seconds", 10)),
            )
            if response.status_code == 404:
                continue
            response.raise_for_status()
            candidate_payload = response.json()
            rows = candidate_payload.get("data") if isinstance(candidate_payload, dict) else None
            if isinstance(rows, list) and rows:
                payload = candidate_payload
                quote_date = candidate
                break
        if payload is None or quote_date is None:
            raise ValueError("no custom market data within lookback window")

        valid_rows: list[tuple[dict[str, Any], float]] = []
        for row in payload["data"]:
            if not isinstance(row, dict):
                continue
            try:
                value = float(row["Return"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                valid_rows.append((row, value))
        if not valid_rows:
            raise ValueError("custom market data has no valid Return values")

        last_row, last_return = valid_rows[-1]
        exposure = float(last_row["Exposure"])
        if not math.isfinite(exposure):
            raise ValueError("custom market data has invalid Exposure")
        is_closed = quote_date < current.date() or current.timetz().replace(tzinfo=None) >= sessions[-1][1]
        delay_minutes = 0
        if not is_closed:
            try:
                quoted_at = datetime.combine(
                    quote_date,
                    dtime.fromisoformat(str(last_row["Time"])),
                    tzinfo=zone,
                )
                delay = round((current - quoted_at).total_seconds() / 60)
                delay_minutes = delay if delay >= 2 else 0
            except (KeyError, TypeError, ValueError):
                pass
        quote = MarketQuote(
            symbol=ascii_text(symbol, "SYM"),
            price=f"{exposure * 100:.0f}%",
            change=f"{last_return * 100:+.2f}%",
            intraday=_custom_minute_returns(valid_rows, quote_date, sessions, current),
            is_closed=is_closed,
            trading_progress=1.0 if is_closed else _custom_trading_progress(quote_date, sessions, current),
            delay_minutes=delay_minutes,
        )
        logger.info(
            "Custom market quote fetched: symbol=%s date=%s price=%s change=%s intraday_points=%d closed=%s",
            quote.symbol,
            quote_date.isoformat(),
            quote.price,
            quote.change,
            len(quote.intraday),
            quote.is_closed,
        )
        return quote
    except Exception as exc:
        quote = MarketQuote(
            symbol=ascii_text(symbol, "SYM"),
            price="--",
            change="--",
            status=ascii_text(exc, "Failed"),
        )
        logger.warning("Custom market quote failed: symbol=%s error=%s", quote.symbol, quote.status)
        return quote


def _fetch_market_quote(
    spec: str,
    denoise_symbols: set[str] | None = None,
    custom_symbols: dict[str, dict[str, Any]] | None = None,
) -> MarketQuote:
    denoise_symbols = denoise_symbols or set()
    custom_symbols = custom_symbols or {}
    primary, fallback = parse_symbol_spec(spec)
    fetch_primary = _fetch_custom_quote if primary in custom_symbols else _fetch_single_quote
    quote = (
        fetch_primary(primary, custom_symbols[primary], primary in denoise_symbols)
        if primary in custom_symbols
        else fetch_primary(primary, primary in denoise_symbols)
    )
    if not fallback:
        return quote

    # Show the primary symbol (e.g. an index) while its market is open;
    # once it closes, switch to the fallback symbol (e.g. its futures).
    if quote.status == "OK" and not quote.is_closed:
        return quote

    fetch_fallback = _fetch_custom_quote if fallback in custom_symbols else _fetch_single_quote
    fallback_quote = (
        fetch_fallback(fallback, custom_symbols[fallback], fallback in denoise_symbols)
        if fallback in custom_symbols
        else fetch_fallback(fallback, fallback in denoise_symbols)
    )
    if fallback_quote.status == "OK":
        # When the primary market is closed we keep showing the fallback (e.g.
        # futures) as the main quote, and add the primary's price/change as a
        # secondary line so both values stay visible.
        if quote.status == "OK":
            return replace(
                fallback_quote,
                secondary_symbol=quote.symbol,
                secondary_price=quote.price,
                secondary_change=quote.change,
            )
        return fallback_quote
    return quote


def _prev_close_from_info(symbol: str, fallback: float) -> float:
    try:
        info = yf.Ticker(symbol).fast_info
        prev = getattr(info, "regular_market_previous_close", None) or getattr(info, "previous_close", None)
        if prev is not None and float(prev) > 0:
            return float(prev)
    except Exception:
        pass
    return fallback


def fetch_market_quotes(
    symbols: list[str],
    denoise_symbols: list[str] | None = None,
    custom_symbols: dict[str, dict[str, Any]] | None = None,
) -> list[MarketQuote]:
    if not symbols:
        return []

    denoise_set = set(denoise_symbols or [])

    try:
        cache_dir = Path(".cache") / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass

    quotes: list[MarketQuote] = []
    for symbol in symbols:
        quotes.append(_fetch_market_quote(symbol, denoise_set, custom_symbols))
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
                "current": "temperature_2m,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
            timeout=10,
        )
        weather.raise_for_status()
        current = weather.json().get("current") or {}
        temp = current.get("temperature_2m")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code")
        report = WeatherReport(
            title=location,
            temperature=f"{float(temp):.1f} C" if temp is not None else "-- C",
            wind=f"Wind {float(wind):.1f} km/h" if wind is not None else "Wind --",
            weather_code=int(code) if code is not None else None,
        )
        logger.info(
            "Weather fetched: title=%s temperature=%s wind=%s code=%s",
            report.title,
            report.temperature,
            report.wind,
            report.weather_code,
        )
        return report
    except Exception as exc:
        report = WeatherReport(title=location, temperature="-- C", wind="Wind --", status=ascii_text(exc, "Failed"))
        logger.warning("Weather failed: title=%s error=%s", report.title, report.status)
        return report


def _window_not_started(window: dict[str, Any] | None) -> bool:
    """A window that reports used==1 with reset==limit has not begun yet."""
    if not window:
        return False
    used = window.get("used_percent")
    limit_seconds = window.get("limit_window_seconds")
    reset_seconds = window.get("reset_after_seconds")
    return bool(
        used == 1
        and limit_seconds
        and reset_seconds
        and int(limit_seconds) == int(reset_seconds)
    )


def _format_window(window: dict[str, Any] | None, short_reset: bool = False) -> str:
    if not window:
        return "N/A"

    used = window.get("used_percent")
    reset_seconds = window.get("reset_after_seconds")
    if _window_not_started(window):
        used = 0
        note = "not started"
    else:
        note = "used"

    reset_at = window.get("reset_at")
    if reset_at:
        if short_reset and reset_seconds:
            s = int(reset_seconds)
            h = s // 3600
            m = (s % 3600) // 60
            reset = f"in {h}h {m}m"
        else:
            reset = datetime.fromtimestamp(int(reset_at), tz=timezone.utc).astimezone().strftime("%a %H:%M")
        return f"{used}% {note}, reset {reset}"
    return f"{used}% {note}"


def _work_seconds(start: datetime, end: datetime) -> int:
    """Count effective working seconds between two timestamps (local tz).

    Working hours (server local time): Mon-Fri, 09:00-11:30, 13:00-18:00.
    Weekends, lunch break and non-working hours are excluded.
    No statutory holidays are excluded.
    """
    if start >= end:
        return 0
    local_start = start.astimezone()
    local_end = end.astimezone()

    morning_start = dtime(9, 0)
    morning_end = dtime(11, 30)
    afternoon_start = dtime(13, 0)
    afternoon_end = dtime(18, 0)

    total = 0.0
    day_delta = timedelta(days=1)
    d = local_start.date()
    end_d = local_end.date()

    while d <= end_d:
        if d.weekday() < 5:
            def _at(t: time) -> datetime:
                return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=local_start.tzinfo)

            ms, me = _at(morning_start), _at(morning_end)
            s = max(local_start, ms)
            e = min(local_end, me)
            if s < e:
                total += (e - s).total_seconds()

            as_, ae = _at(afternoon_start), _at(afternoon_end)
            s = max(local_start, as_)
            e = min(local_end, ae)
            if s < e:
                total += (e - s).total_seconds()

        d += day_delta

    return int(total)


def _window_progress(window: dict[str, Any] | None) -> tuple[int, int]:
    """Compute (natural_time_progress, effective_work_progress) in percent.

    Returns (0, 0) when data is missing or cannot be computed.
    """
    if not window:
        return (0, 0)

    limit_seconds = window.get("limit_window_seconds")
    reset_after = window.get("reset_after_seconds")
    reset_at = window.get("reset_at")

    if not limit_seconds or not reset_after or not reset_at:
        return (0, 0)

    try:
        limit_seconds = int(limit_seconds)
        reset_after = int(reset_after)
        reset_at_ts = int(reset_at)

        t = max(0, min(100, int(round((limit_seconds - reset_after) / limit_seconds * 100))))

        reset_dt = datetime.fromtimestamp(reset_at_ts, tz=timezone.utc)
        window_start = reset_dt - timedelta(seconds=limit_seconds)
        now = datetime.now(timezone.utc)

        total_work = _work_seconds(window_start, reset_dt)
        if total_work > 0:
            elapsed_work = _work_seconds(window_start, now)
            e = max(0, min(100, int(round(elapsed_work / total_work * 100))))
        else:
            e = 0
    except Exception:
        return (0, 0)

    return (t, e)


CODEX_NOT_STARTED_REFRESH_SECONDS = 300


def should_refresh_codex(
    previous: CodexUsage | None,
    throttle_seconds: int = CODEX_NOT_STARTED_REFRESH_SECONDS,
    now: float | None = None,
) -> bool:
    """Decide whether to hit the Codex usage API again.

    When the 5H (primary) window has not started yet we only refresh every
    ``throttle_seconds`` to avoid hammering the OpenAI endpoint each minute.
    Once the window is active (or on any error/missing data) we refresh on
    every build as usual.
    """
    if previous is None:
        return True
    if previous.status != "OK":
        return True
    if not previous.not_started:
        return True
    if previous.fetched_at is None:
        return True
    if now is None:
        now = time.time()
    return (now - previous.fetched_at) >= throttle_seconds


def fetch_codex_usage(config: dict[str, Any]) -> CodexUsage:
    try:
        token = normalize_token(config.get("token"))
    except ValueError as exc:
        logger.warning("Codex usage skipped: invalid token: %s", exc)
        return CodexUsage(primary="Invalid token", secondary="N/A", allowed="N/A", status="Invalid token")
    token_info = inspect_token(token)
    token_fields = {
        "token_expires_at": token_info["expires_at"],
        "token_expiring_soon": token_info["expiring_soon"],
        "token_expired": token_info["expired"],
    }
    if not token:
        logger.info("Codex usage skipped: no token configured")
        return CodexUsage(primary="No token", secondary="N/A", allowed="N/A", status="Skipped", **token_fields)

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
        primary_window = rate_limit.get("primary_window")
        secondary_window = rate_limit.get("secondary_window")
        primary_t, primary_e = _window_progress(primary_window)
        secondary_t, secondary_e = _window_progress(secondary_window)
        usage = CodexUsage(
            primary=_format_window(primary_window, short_reset=True),
            secondary=_format_window(secondary_window),
            allowed="yes" if rate_limit.get("allowed") else "no",
            primary_t=primary_t,
            primary_e=primary_e,
            secondary_t=secondary_t,
            secondary_e=secondary_e,
            not_started=_window_not_started(primary_window),
            fetched_at=time.time(),
            **token_fields,
        )
        logger.info(
            "Codex usage fetched: primary=%s secondary=%s allowed=%s",
            usage.primary,
            usage.secondary,
            usage.allowed,
        )
        return usage
    except Exception as exc:
        usage = CodexUsage(
            primary="Unavailable",
            secondary="Unavailable",
            allowed="N/A",
            status=ascii_text(exc, "Failed"),
            **token_fields,
        )
        logger.warning("Codex usage failed: error=%s", usage.status)
        return usage
