from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .data import CodexUsage, MarketQuote, TodoSummary, WeatherReport
from .text import ascii_text


WIDTH = 1080
HEIGHT = 1440

DASH_X = 8
DASH_Y = 8
DASH_W = 1064
DASH_H = 1424
INK = 0
PAPER = 255
MID = 70
LIGHT = 180
LINE_W = 2

IMG_DIR = Path(__file__).resolve().parent.parent / "img"
WEATHER_ICON_DIR = IMG_DIR / "weather"

WMO_TO_ICON: dict[int, int] = {
    0: 1,
    1: 2,
    2: 2,
    3: 3,
    45: 19,
    48: 19,
    51: 8,
    53: 9,
    55: 9,
    56: 7,
    57: 7,
    61: 8,
    63: 9,
    65: 10,
    66: 20,
    67: 20,
    71: 15,
    73: 16,
    75: 17,
    77: 15,
    80: 4,
    81: 4,
    82: 10,
    85: 14,
    86: 14,
    95: 5,
    96: 6,
    99: 6,
}


@dataclass
class DashboardData:
    generated_at: datetime
    market: list[MarketQuote]
    weather: WeatherReport
    codex: CodexUsage
    todos: TodoSummary


def _font(size: int, bold: bool = False, cjk: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cjk_candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    latin_candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    candidates = (cjk_candidates + latin_candidates) if cjk else latin_candidates
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = _bbox(draw, text, font)
    return box[2] - box[0]


def _text_h(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = _bbox(draw, text, font)
    return box[3] - box[1]


def _render_text(text: object, fallback: str = "N/A", *, sanitize: bool = True) -> str:
    if sanitize:
        return ascii_text(text, fallback)
    value = str(text) if text is not None else ""
    value = re.sub(r"\s+", " ", value).strip()
    return value or fallback


def _truncate(draw: ImageDraw.ImageDraw, text: object, font: ImageFont.ImageFont, max_width: int, *, sanitize: bool = True) -> str:
    value = _render_text(text, sanitize=sanitize)
    if _text_w(draw, value, font) <= max_width:
        return value

    suffix = "..."
    while value and _text_w(draw, value + suffix, font) > max_width:
        value = value[:-1].rstrip()
    return (value + suffix) if value else suffix


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    size: int,
    *,
    bold: bool = False,
    cjk: bool = False,
    min_size: int = 12,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font = _font(size, bold=bold, cjk=cjk)
    while size > min_size and _text_w(draw, text, font) > max_width:
        size -= 1
        font = _font(size, bold=bold, cjk=cjk)
    return font


def _draw_dashed_hline(
    draw: ImageDraw.ImageDraw,
    x_start: int,
    x_end: int,
    y: int,
    *,
    fill: int,
    width: int,
    dash: int = 14,
    gap: int = 9,
) -> None:
    x = x_start
    while x < x_end:
        seg_end = min(x + dash, x_end)
        draw.line((x, y, seg_end, y), fill=fill, width=width)
        x += dash + gap


def _draw_fit(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: object,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    fill: int = INK,
    anchor: str | None = None,
    sanitize: bool = True,
) -> None:
    draw.text(xy, _truncate(draw, text, font, max_width, sanitize=sanitize), fill=fill, font=font, anchor=anchor)


def _section_title(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    meta: str,
) -> int:
    x1, y1, x2, _ = rect
    title_font = _font(20, bold=True)
    meta_font = _font(15)
    title_y = y1 + 33
    title_text = ascii_text(title).upper()
    meta_text = ascii_text(meta).upper()
    draw.text((x1 + 46, title_y), title_text, fill=INK, font=title_font)
    meta_w = _text_w(draw, meta_text, meta_font)
    meta_x = x2 - 46 - meta_w
    draw.text((meta_x, title_y + 3), meta_text, fill=INK, font=meta_font)
    rule_x1 = x1 + 46 + _text_w(draw, title_text, title_font) + 22
    rule_x2 = meta_x - 22
    if rule_x2 > rule_x1:
        draw.rectangle((rule_x1, title_y + 11, rule_x2, title_y + 12), fill=INK)
    return title_y + 34


def _draw_cloud_icon(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    h = y2 - y1
    stroke = 5
    draw.arc((x1 + int(w * 0.05), y1 + int(h * 0.38), x1 + int(w * 0.45), y1 + int(h * 0.86)), 160, 350, fill=INK, width=stroke)
    draw.arc((x1 + int(w * 0.22), y1 + int(h * 0.18), x1 + int(w * 0.72), y1 + int(h * 0.76)), 185, 360, fill=INK, width=stroke)
    draw.arc((x1 + int(w * 0.50), y1 + int(h * 0.34), x1 + int(w * 0.96), y1 + int(h * 0.86)), 190, 25, fill=INK, width=stroke)
    draw.line((x1 + int(w * 0.18), y1 + int(h * 0.72), x1 + int(w * 0.80), y1 + int(h * 0.72)), fill=INK, width=stroke)
    draw.line((x1 + int(w * 0.25), y2 - 6, x2 - int(w * 0.20), y2 - 6), fill=INK, width=stroke)


def _draw_clock_icon(draw: ImageDraw.ImageDraw, center: tuple[int, int], size: int = 28) -> None:
    """Draw a high-contrast clock indicator suitable for an e-ink display."""
    cx, cy = center
    radius = size // 2
    stroke = max(2, size // 10)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=INK, width=stroke)
    draw.line((cx, cy, cx, cy - radius // 2), fill=INK, width=stroke)
    draw.line((cx, cy, cx + radius // 2, cy + radius // 3), fill=INK, width=stroke)
    draw.ellipse((cx - stroke, cy - stroke, cx + stroke, cy + stroke), fill=INK)


def _token_needs_attention(codex: CodexUsage) -> bool:
    return codex.token_expiring_soon or codex.token_expired


def _load_weather_icon(code: int | None, size: int = 78) -> Image.Image | None:
    if code is None:
        return None
    icon_num = WMO_TO_ICON.get(code)
    if icon_num is None:
        return None
    matches = list(WEATHER_ICON_DIR.glob(f"weather_{icon_num}*.svg"))
    if not matches:
        return None
    svg_path = matches[0]
    try:
        import resvg_py

        svg_str = svg_path.read_text(encoding="utf-8")
        png_bytes = resvg_py.svg_to_bytes(svg_str, width=size * 2, height=size * 2, background="#ffffff")
        img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((size, size), Image.LANCZOS)
        return img
    except Exception:
        return None


def _draw_hero(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], data: DashboardData) -> None:
    x1, y1, x2, y2 = rect
    split = x1 + int((x2 - x1) * 0.60)

    time_x = x1 + 52
    time_y = y1 + 50
    date_font = _font(26, bold=True)
    time_font = _font(158, bold=True)
    date_left = data.generated_at.strftime("%b %d, %Y").upper()
    date_right = data.generated_at.strftime("%A").upper()
    draw.text((time_x, time_y), f"{date_left}  {date_right}", fill=INK, font=date_font)
    draw.text((time_x - 6, y1 + 98), data.generated_at.strftime("%H:%M"), fill=INK, font=time_font)

    weather_x = split + 38
    weather_right = x2 - 38
    label_font = _font(22, bold=True)
    temp_font = _font(102, bold=True)
    detail_font = _font(20)
    label = ascii_text(data.weather.title, "WEATHER").upper()
    _draw_fit(draw, (weather_x, y1 + 53), label, label_font, weather_right - weather_x - 94)
    icon_size = 78
    icon_rect = (weather_right - icon_size, y1 + 38, weather_right, y1 + 38 + icon_size)
    weather_icon = _load_weather_icon(data.weather.weather_code, size=icon_size)
    if weather_icon is not None:
        inverted = ImageOps.invert(weather_icon)
        draw.bitmap((icon_rect[0], icon_rect[1]), inverted, fill=INK)
    else:
        _draw_cloud_icon(draw, icon_rect)

    temp = ascii_text(data.weather.temperature, "-- C")
    temp = re.sub(r"\s*C$", " C", temp).strip()
    _draw_fit(draw, (weather_x, y1 + 120), temp, temp_font, weather_right - weather_x)

    if data.weather.status != "OK":
        _draw_fit(draw, (weather_x, y2 - 55), ascii_text(data.weather.status).upper(), detail_font, weather_right - weather_x, fill=INK)


def _draw_market(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], quotes: list[MarketQuote]) -> None:
    x1, y1, x2, y2 = rect
    rows = quotes[:8] or [MarketQuote(symbol="No symbols", price="--", change="--")]
    row_top = y1 + 8
    row_h = max(56, (y2 - row_top - 48) // 4)
    gap = 56
    col_w = ((x2 - x1) - 92 - gap) // 2
    symbol_font = _font(36, bold=True)
    price_font = _font(28)
    change_font = _font(28, bold=True)
    secondary_font = _font(20, bold=True)

    sep_x = x1 + 46 + col_w + gap // 2
    draw.line((sep_x, row_top, sep_x, row_top + 4 * row_h), fill=LIGHT, width=1)

    for index, quote in enumerate(rows):
        col = index % 2
        row = index // 2
        rx = x1 + 46 + col * (col_w + gap)
        ry = row_top + row * row_h
        if row < 3:
            draw.line((rx, ry + row_h - 1, rx + col_w, ry + row_h - 1), fill=LIGHT, width=1)

        symbol = ascii_text(quote.symbol, "SYM")
        price_raw = ascii_text(quote.price, "--")
        change_raw = ascii_text(quote.change if quote.status == "OK" else "N/A", "--")
        change_w = 130
        change = _truncate(draw, change_raw, change_font, change_w)
        change_px = _text_w(draw, change, change_font)
        price_gap = 16
        price_max = col_w - change_px - price_gap - 10
        price = _truncate(draw, price_raw, price_font, price_max)
        price_px = _text_w(draw, price, price_font)
        baseline = ry + (row_h - _text_h(draw, symbol, symbol_font)) // 2 - 2
        _draw_market_symbol(
            draw,
            (rx, baseline),
            symbol,
            symbol_font,
            col_w - price_px - change_px - price_gap - 10,
            quote.is_closed,
        )
        has_secondary = bool(quote.secondary_price)
        price_x = rx + col_w - change_px - price_gap
        change_x = rx + col_w
        price_dy = -18 if has_secondary else 0
        draw.text((price_x, baseline + 4 + price_dy), price, font=price_font, fill=INK, anchor="ra")
        draw.text((change_x, baseline + 6 + price_dy), change, font=change_font, fill=INK, anchor="ra")
        if has_secondary:
            sec_price = _truncate(draw, ascii_text(quote.secondary_price, "--"), secondary_font, price_max)
            sec_change = _truncate(draw, ascii_text(quote.secondary_change, "--"), secondary_font, change_w)
            sec_y = baseline + 6 + price_dy + 38
            draw.text((price_x, sec_y), sec_price, font=secondary_font, fill=INK, anchor="ra")
            draw.text((change_x, sec_y), sec_change, font=secondary_font, fill=INK, anchor="ra")


def _draw_market_symbol(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    symbol: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    is_closed: bool,
    delay_minutes: int = 0,
    is_24h: bool = False,
) -> None:
    x, y = xy
    _draw_fit(draw, xy, symbol, font, max_width)
    marker_gap = 5
    right_edge = x - marker_gap
    symbol_h = _text_h(draw, symbol, font)
    if is_24h and delay_minutes > 0:
        top = "24"
        bottom = f"-{delay_minutes}"
        top_font = _fit_font(draw, top, 40, 18, bold=True)
        bottom_font = _fit_font(draw, bottom, 40, 18, bold=True)
        top_h = _text_h(draw, top, top_font)
        bottom_h = _text_h(draw, bottom, bottom_font)
        line_gap = 2
        top_y = y + (symbol_h - (top_h + line_gap + bottom_h)) // 2
        draw.text(
            (right_edge - _text_w(draw, top, top_font), top_y),
            top,
            font=top_font,
            fill=INK,
        )
        draw.text(
            (right_edge - _text_w(draw, bottom, bottom_font), top_y + top_h + line_gap),
            bottom,
            font=bottom_font,
            fill=INK,
        )
        return
    marker = ""
    marker_font = font
    if is_24h:
        marker = "24"
        marker_font = _fit_font(draw, marker, 40, 22, bold=True)
    elif is_closed:
        marker = "*"
    elif delay_minutes > 0:
        marker = f"-{delay_minutes}"
        marker_font = _fit_font(draw, marker, 40, 22, bold=True)
    if marker:
        marker_y = y + (symbol_h - _text_h(draw, marker, marker_font)) // 2
        draw.text(
            (right_edge - _text_w(draw, marker, marker_font), marker_y),
            marker,
            font=marker_font,
            fill=INK,
        )


def _usage_percent(text: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return 0
    return max(0, min(100, int(round(float(match.group(1))))))


def _usage_note(text: str, status: str) -> str:
    if status != "OK":
        return status
    clean = ascii_text(text, "N/A")
    if "not started" in clean.lower():
        return "Not started"
    if "reset" in clean.lower():
        return clean.split(",", 1)[-1].strip()
    pct = _usage_percent(clean)
    if pct >= 80:
        return "Heavy activity"
    if pct >= 45:
        return "Keep an eye on it"
    return "Moderate activity"


def _draw_meter(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    percent: int,
    t_progress: int = 0,
    e_progress: int = 0,
) -> None:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    draw.rectangle(rect, outline=INK, width=LINE_W)
    inner_x = x1 + 3
    inner_y = y1 + 3
    inner_w = x2 - x1 - 6
    inner_h = y2 - y1 - 6
    gap = 1
    segment_w = (inner_w - 9 * gap) // 10
    filled = int(math.ceil(percent / 10))
    for i in range(10):
        sx = inner_x + i * (segment_w + gap)
        if i < filled:
            draw.rectangle((sx, inner_y, sx + segment_w, inner_y + inner_h), fill=INK)

    arrow_h = 14
    arrow_hw = 10
    arrow_gap = 3
    border_w = 2

    def _clamp_x(pct: int) -> int:
        ax = x1 + int(w * max(0, min(100, pct)) / 100)
        return max(x1 + arrow_hw, min(x2 - arrow_hw, ax))

    tx = _clamp_x(t_progress)
    utip = [
        (tx - arrow_hw, y1 - arrow_gap - arrow_h),
        (tx + arrow_hw, y1 - arrow_gap - arrow_h),
        (tx, y1 - arrow_gap),
    ]
    if percent >= max(t_progress, 0):
        for i in range(3):
            draw.line([utip[i], utip[(i + 1) % 3]], fill=INK, width=border_w)
    else:
        draw.polygon(utip, fill=INK)

    ex = _clamp_x(e_progress)
    ltip = [
        (ex - arrow_hw, y2 + arrow_gap + arrow_h),
        (ex + arrow_hw, y2 + arrow_gap + arrow_h),
        (ex, y2 + arrow_gap),
    ]
    if percent >= max(e_progress, 0):
        for i in range(3):
            draw.line([ltip[i], ltip[(i + 1) % 3]], fill=INK, width=border_w)
    else:
        draw.polygon(ltip, fill=INK)


def _draw_usage_card(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    name: str,
    value: str,
    status: str,
    t_progress: int = 0,
    e_progress: int = 0,
    token_attention: bool = False,
) -> None:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    cx = x1 + w // 2
    name_font = _font(34, bold=True)
    pct_font = _font(140, bold=True)
    foot_font = _font(28)
    percent = _usage_percent(value)
    pct_text = f"{percent}%"
    pct_h = _text_h(draw, pct_text, pct_font)
    name_text = ascii_text(name).upper()
    name_w = _text_w(draw, name_text, name_font)
    icon_size = 30
    icon_gap = 10
    group_w = name_w + (icon_gap + icon_size if token_attention else 0)
    group_left = cx - group_w // 2
    draw.text((group_left + name_w // 2, y1 + 10), name_text, fill=INK, font=name_font, anchor="mt")
    if token_attention:
        icon_cx = group_left + name_w + icon_gap + icon_size // 2
        _draw_clock_icon(draw, (icon_cx, y1 + 10 + icon_size // 2), icon_size)
    pct_y = y1 + 88
    draw.text((cx, pct_y), pct_text, fill=INK, font=pct_font, anchor="mt")
    bar_w = min(w - 40, 520)
    bar_h = 36
    bar_x = cx - bar_w // 2
    bar_y = pct_y + pct_h + 28
    _draw_meter(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), percent, t_progress, e_progress)
    note = ascii_text(_usage_note(value, status)).upper()
    draw.text((cx, bar_y + bar_h + 30), note, fill=INK, font=foot_font, anchor="mt")


def _draw_focus(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], codex: CodexUsage) -> None:
    x1, y1, x2, y2 = rect
    gap = 46
    card_w = ((x2 - x1) - 92 - gap) // 2
    top = y1 + 32
    bottom = y2 - 8
    _draw_usage_card(
        draw,
        (x1 + 46, top, x1 + 46 + card_w, bottom),
        "5H",
        codex.primary,
        codex.status,
        codex.primary_t,
        codex.primary_e,
        _token_needs_attention(codex),
    )
    _draw_usage_card(draw, (x1 + 46 + card_w + gap, top, x2 - 46, bottom), "Weekly", codex.secondary, codex.status, codex.secondary_t, codex.secondary_e)


def _draw_time_landscape(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], data: DashboardData) -> None:
    x1, y1, x2, y2 = rect
    cx = x1 + (x2 - x1) // 2
    date_font = _font(26, bold=True)
    time_font = _font(158, bold=True)
    date_left = data.generated_at.strftime("%b %d, %Y").upper()
    date_right = data.generated_at.strftime("%A").upper()
    draw.text((cx, y1 + 50), f"{date_left}  {date_right}", fill=INK, font=date_font, anchor="mt")
    draw.text((cx, y1 + 98), data.generated_at.strftime("%H:%M"), fill=INK, font=time_font, anchor="mt")


def _draw_weather_landscape(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], data: DashboardData) -> None:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    cx = x1 + w // 2
    section_h = y2 - y1
    label_font = _font(24, bold=True)
    temp_font = _font(68, bold=True)
    detail_font = _font(18)
    label = ascii_text(data.weather.title, "WEATHER").upper()
    label_h = _text_h(draw, label, label_font)
    temp_str = ascii_text(data.weather.temperature, "-- C")
    temp_str = re.sub(r"\s*C$", " C", temp_str).strip()
    temp_w = _text_w(draw, temp_str, temp_font)
    temp_h = _text_h(draw, temp_str, temp_font)
    icon_size = 52
    row_gap = 16
    label_gap = 24
    content_h = label_h + label_gap + max(temp_h, icon_size)
    offset = (section_h - content_h) // 2
    label_y = y1 + offset
    _draw_fit(draw, (cx, label_y), label, label_font, w - 40, anchor="mt")
    row_y = label_y + label_h + label_gap
    row_w = temp_w + row_gap + icon_size
    row_start = cx - row_w // 2
    temp_x = row_start + temp_w
    temp_y = row_y
    draw.text((temp_x, temp_y), temp_str, fill=INK, font=temp_font, anchor="ra")
    icon_x = row_start + temp_w + row_gap
    temp_box = draw.textbbox((temp_x, temp_y), temp_str, font=temp_font, anchor="ra")
    icon_y = (temp_box[1] + temp_box[3] - icon_size) // 2
    icon_rect = (icon_x, icon_y, icon_x + icon_size, icon_y + icon_size)
    weather_icon = _load_weather_icon(data.weather.weather_code, size=icon_size)
    if weather_icon is not None:
        inverted = ImageOps.invert(weather_icon)
        draw.bitmap((icon_rect[0], icon_rect[1]), inverted, fill=INK)
    else:
        _draw_cloud_icon(draw, icon_rect)
    if data.weather.status != "OK":
        _draw_fit(draw, (cx, y2 - 20), ascii_text(data.weather.status).upper(), detail_font, w - 40, anchor="mt", fill=INK)


def _draw_usage_card_compact(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    name: str,
    value: str,
    status: str,
    t_progress: int = 0,
    e_progress: int = 0,
    token_attention: bool = False,
) -> None:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    cx = x1 + w // 2
    name_font = _font(26, bold=True)
    pct_font = _font(72, bold=True)
    foot_font = _font(20)
    percent = _usage_percent(value)
    pct_text = f"{percent}%"
    pct_h = _text_h(draw, pct_text, pct_font)
    name_text = ascii_text(name).upper()
    name_w = _text_w(draw, name_text, name_font)
    icon_size = 24
    icon_gap = 9
    group_w = name_w + (icon_gap + icon_size if token_attention else 0)
    group_left = cx - group_w // 2
    draw.text((group_left + name_w // 2, y1 + 8), name_text, fill=INK, font=name_font, anchor="mt")
    if token_attention:
        icon_cx = group_left + name_w + icon_gap + icon_size // 2
        _draw_clock_icon(draw, (icon_cx, y1 + 8 + icon_size // 2), icon_size)
    pct_y = y1 + 48
    draw.text((cx, pct_y), pct_text, fill=INK, font=pct_font, anchor="mt")
    bar_w = min(w - 40, 400)
    bar_h = 28
    bar_x = cx - bar_w // 2
    bar_y = pct_y + pct_h + 24
    _draw_meter(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), percent, t_progress, e_progress)
    note = ascii_text(_usage_note(value, status)).upper()
    draw.text((cx, bar_y + bar_h + 30), note, fill=INK, font=foot_font, anchor="mt")


def _draw_focus_landscape(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], codex: CodexUsage) -> None:
    x1, y1, x2, y2 = rect
    h = y2 - y1
    gap = 16
    card_h = (h - gap) // 2

    pct_font = _font(72, bold=True)
    foot_font = _font(20)
    note_h = _text_h(draw, "MODERATE ACTIVITY", foot_font)
    pct_h = _text_h(draw, "88%", pct_font)
    bar_h = 28
    content_top = 8
    content_bottom = 48 + pct_h + 24 + bar_h + 30 + note_h
    content_h = content_bottom - content_top
    top_pad = max(0, (card_h - content_h) // 2)

    top_rect = (x1, y1 + top_pad, x2, y2)
    _draw_usage_card_compact(
        draw,
        top_rect,
        "5H",
        codex.primary,
        codex.status,
        codex.primary_t,
        codex.primary_e,
        _token_needs_attention(codex),
    )

    div_y = y1 + card_h + gap // 2
    draw.line((x1, div_y, x2, div_y), fill=LIGHT, width=1)

    bot_y1 = y1 + card_h + gap
    bot_rect = (x1, bot_y1 + top_pad, x2, y2)
    _draw_usage_card_compact(draw, bot_rect, "Weekly", codex.secondary, codex.status, codex.secondary_t, codex.secondary_e)


def _draw_intraday_curve(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    values: list[float],
    progress: float = 1.0,
) -> None:
    x1, y1, x2, y2 = rect
    if x2 <= x1 or y2 <= y1:
        return

    progress = max(0.0, min(1.0, float(progress)))
    x2_effective = x1 + max(1, round(progress * (x2 - x1)))

    if len(values) < 2:
        cy = (y1 + y2) // 2
        draw.line((x1, cy, x2_effective, cy), fill=LIGHT, width=1)
        return

    finite = [float(value) for value in values if math.isfinite(float(value))]
    if len(finite) < 2:
        return
    low = min(min(finite), 0.0)
    high = max(max(finite), 0.0)
    if high == low:
        high += 0.5
        low -= 0.5
    padding = max((high - low) * 0.08, 0.02)
    low -= padding
    high += padding

    def point(index: int, value: float) -> tuple[int, int]:
        px = x1 + round(index * (x2_effective - x1) / (len(finite) - 1))
        py = y2 - round((value - low) * (y2 - y1) / (high - low))
        return px, py

    zero_y = point(0, 0.0)[1]
    draw.line((x1, zero_y, x2, zero_y), fill=LIGHT, width=3)
    points = [point(index, value) for index, value in enumerate(finite)]
    draw.line(points, fill=INK, width=3, joint="curve")


def _draw_market_landscape(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], quotes: list[MarketQuote]) -> None:
    x1, y1, x2, y2 = rect
    rows = quotes[:8] or [MarketQuote(symbol="No symbols", price="--", change="--")]
    row_top = y1 + 8
    row_h = max(56, (y2 - row_top - 48) // len(rows))
    symbol_size = 31
    price_size = 26
    change_size = 28
    symbol_font = _font(symbol_size, bold=True)
    right_edge = x2 - 46
    change_w = 124
    price_w = 126
    symbol_w = 188
    col_gap = 18
    content_left = x1 + 46
    change_left = right_edge - change_w
    price_left = change_left - col_gap - price_w
    chart_left = content_left + symbol_w + col_gap
    chart_right = price_left - col_gap
    for index, quote in enumerate(rows):
        ry = row_top + index * row_h
        if index < len(rows) - 1:
            _draw_dashed_hline(draw, x1 + 46, right_edge, ry + row_h - 1, fill=LIGHT, width=3)
        symbol = ascii_text(quote.symbol, "SYM")
        price_raw = ascii_text(quote.price, "--")
        change_raw = ascii_text(quote.change if quote.status == "OK" else "N/A", "--")
        symbol_fit = _fit_font(draw, symbol, symbol_w, symbol_size, bold=True)
        price_fit = _fit_font(draw, price_raw, price_w, price_size)
        change_fit = _fit_font(draw, change_raw, change_w, change_size, bold=True)
        baseline = ry + (row_h - _text_h(draw, symbol, symbol_font)) // 2 - 2
        symbol_baseline = ry + (row_h - _text_h(draw, symbol, symbol_fit)) // 2 - 2
        _draw_market_symbol(
            draw, (content_left, symbol_baseline), symbol, symbol_fit, symbol_w, quote.is_closed, quote.delay_minutes, quote.is_24h
        )
        chart_h = min(78, row_h - 34)
        chart_top = ry + (row_h - chart_h) // 2
        _draw_intraday_curve(draw, (chart_left, chart_top, chart_right, chart_top + chart_h), quote.intraday, quote.trading_progress)
        has_secondary = bool(quote.secondary_price)
        price_dy = -16 if has_secondary else 0
        draw.text((price_left + price_w, baseline + 4 + price_dy), price_raw, font=price_fit, fill=INK, anchor="ra")
        draw.text((right_edge, baseline + 6 + price_dy), change_raw, font=change_fit, fill=INK, anchor="ra")
        if has_secondary:
            secondary_font = _font(19, bold=True)
            sec_price = _truncate(draw, ascii_text(quote.secondary_price, "--"), secondary_font, price_w)
            sec_change = _truncate(draw, ascii_text(quote.secondary_change, "--"), secondary_font, change_w)
            sec_y = baseline + 6 + price_dy + 36
            draw.text((price_left + price_w, sec_y), sec_price, font=secondary_font, fill=INK, anchor="ra")
            draw.text((right_edge, sec_y), sec_change, font=secondary_font, fill=INK, anchor="ra")


def _todo_items(todos: TodoSummary) -> list[tuple[str, bool]]:
    open_items = [(item, False) for item in todos.open_items[:6]]
    done_slots = max(0, 6 - len(open_items))
    done = [(item, True) for item in todos.done_items[:done_slots]]
    items = open_items + done
    if not items:
        return [("No open tasks", False)]
    return items[:6]


def _draw_todos(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], todos: TodoSummary) -> None:
    x1, y1, x2, y2 = rect
    items = _todo_items(todos)
    content_y = _section_title(draw, rect, "Today", f"{len(items)} Tasks")
    row_top = content_y + 14
    row_h = max(38, (y2 - row_top - 30) // 3)
    gap = 52
    col_w = ((x2 - x1) - 92 - gap) // 2
    text_font = _font(24, cjk=True)
    index_font = _font(17, bold=True)

    for index, (item, done) in enumerate(items, start=1):
        col = (index - 1) % 2
        row = (index - 1) // 2
        rx = x1 + 46 + col * (col_w + gap)
        ry = row_top + row * row_h
        if row < 2:
            draw.line((rx, ry + row_h - 1, rx + col_w, ry + row_h - 1), fill=LIGHT, width=1)

        box = (rx, ry + (row_h - 35) // 2, rx + 35, ry + (row_h - 35) // 2 + 35)
        if done:
            draw.rectangle(box, outline=INK, width=LINE_W)
            fill = INK
        else:
            draw.rectangle(box, fill=INK)
            fill = PAPER
        draw.text((rx + 17, box[1] + 8), str(index), fill=fill, font=index_font, anchor="ma")

        tx = rx + 50
        ty = ry + (row_h - _text_h(draw, "A", text_font)) // 2 - 1
        text = _truncate(draw, item, text_font, col_w - 55, sanitize=False)
        draw.text((tx, ty), text, fill=INK, font=text_font)
        if done:
            line_y = ty + _text_h(draw, text, text_font) // 2 + 2
            draw.line((tx, line_y, tx + _text_w(draw, text, text_font), line_y), fill=INK, width=2)

    if todos.status != "OK":
        status_font = _font(14)
        _draw_fit(draw, (x1 + 46, y2 - 24), f"TODO STATUS: {todos.status}", status_font, x2 - x1 - 92, fill=MID)


def _draw_footer(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], generated_at: datetime) -> None:
    x1, y1, x2, y2 = rect
    font = _font(15)
    cy = y1 + (y2 - y1) // 2
    dot = 12
    draw.rectangle((x1 + 46, cy - dot // 2, x1 + 46 + dot, cy - dot // 2 + dot), fill=INK)
    draw.text((x1 + 70, cy), "E-INK OPTIMIZED - MONOCHROME", fill=INK, font=font, anchor="lm")
    refresh = f"REFRESH {generated_at.strftime('%H:%M')}"
    draw.text((x2 - 46, cy), refresh, fill=INK, font=font, anchor="rm")


def render_dashboard(data: DashboardData, output: str | Path, orientation: str = "portrait") -> Path:
    if orientation == "landscape":
        canvas_w, canvas_h = 1440, 1080
        dash_x, dash_y = 8, 8
        dash_w, dash_h = 1424, 1064

        image = Image.new("L", (canvas_w, canvas_h), PAPER)
        draw = ImageDraw.Draw(image)

        left_w = 580
        left_x1 = dash_x
        left_x2 = dash_x + left_w
        col_gap = 24
        right_x1 = left_x2 + col_gap
        right_x2 = dash_x + dash_w

        time_y1 = dash_y
        time_y2 = time_y1 + 280
        _draw_time_landscape(draw, (left_x1, time_y1, left_x2, time_y2), data)
        draw.line((left_x1, time_y2, left_x2, time_y2), fill=INK, width=LINE_W)

        weather_y1 = time_y2
        weather_y2 = weather_y1 + 224
        _draw_weather_landscape(draw, (left_x1, weather_y1, left_x2, weather_y2), data)
        draw.line((left_x1, weather_y2, left_x2, weather_y2), fill=INK, width=LINE_W)

        codex_y1 = weather_y2
        codex_y2 = dash_y + dash_h
        _draw_focus_landscape(draw, (left_x1, codex_y1, left_x2, codex_y2), data.codex)

        sep_x = left_x2 + col_gap // 2
        draw.line((sep_x, dash_y, sep_x, dash_y + dash_h), fill=INK, width=LINE_W)

        _draw_market_landscape(draw, (right_x1, dash_y, right_x2, dash_y + dash_h), data.market)

        image = image.rotate(270, expand=True, fillcolor=PAPER)
    else:
        canvas_w, canvas_h = WIDTH, HEIGHT
        dash_x, dash_y = DASH_X, DASH_Y
        dash_w, dash_h = DASH_W, DASH_H
        section_heights = [280, 440, 704]

        image = Image.new("L", (canvas_w, canvas_h), PAPER)
        draw = ImageDraw.Draw(image)

        x1 = dash_x
        y1 = dash_y
        x2 = dash_x + dash_w
        y2 = dash_y + dash_h

        top = y1
        sections: list[tuple[int, int, int, int]] = []
        for height in section_heights:
            sections.append((x1, top, x2, top + height))
            top += height

        divider_font = _font(26, bold=True)
        divider_labels = [None, "MARKETS"]
        for i, (_, _, _, section_bottom) in enumerate(sections[:-1]):
            label = divider_labels[i]
            line_y = section_bottom
            draw.line((x1, line_y, x2, line_y), fill=INK, width=LINE_W)
            if label is None:
                continue
            label_h = _text_h(draw, label, divider_font)
            label_w = _text_w(draw, label, divider_font)
            pad = 14
            label_cx = x1 + (x2 - x1) // 2
            bx = label_cx - label_w // 2 - pad
            by = line_y - label_h // 2
            bw = label_w + pad * 2
            bh = label_h
            draw.rectangle((bx, by, bx + bw, by + bh), fill=PAPER)
            draw.text((label_cx, line_y), label, fill=INK, font=divider_font, anchor="mm")

        _draw_hero(draw, sections[0], data)
        _draw_focus(draw, sections[1], data.codex)
        _draw_market(draw, sections[2], data.market)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path
