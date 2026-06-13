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
    time_y = y1 + 42
    date_font = _font(26, bold=True)
    note_font = _font(20)
    time_font = _font(158, bold=True)
    date_left = data.generated_at.strftime("%b %d, %Y").upper()
    date_right = data.generated_at.strftime("%A").upper()
    draw.text((time_x, time_y), f"{date_left}  {date_right}", fill=INK, font=date_font)
    draw.text((time_x - 6, y1 + 88), data.generated_at.strftime("%H:%M"), fill=INK, font=time_font)
    draw.text((time_x, y2 - 25), "ASIA / SHANGHAI", fill=INK, font=note_font)

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
    _draw_fit(draw, (weather_x, y1 + 100), temp, temp_font, weather_right - weather_x)

    detail_y = y2 - 55
    details: list[str] = [data.weather.wind]
    if data.weather.status != "OK":
        details.append(data.weather.status)
    for detail in details:
        _draw_fit(draw, (weather_x, detail_y), ascii_text(detail).upper(), detail_font, weather_right - weather_x, fill=INK)
        detail_y += 24


def _draw_market(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], quotes: list[MarketQuote]) -> None:
    x1, y1, x2, y2 = rect
    rows = quotes[:8] or [MarketQuote(symbol="No symbols", price="--", change="--")]
    content_y = _section_title(draw, rect, "Markets", f"{len(quotes)} Symbols")
    row_top = content_y + 12
    row_h = max(42, (y2 - row_top - 32) // 4)
    gap = 54
    col_w = ((x2 - x1) - 92 - gap) // 2
    symbol_font = _font(34, bold=True)
    price_font = _font(26)
    change_font = _font(26, bold=True)

    for index, quote in enumerate(rows):
        col = index % 2
        row = index // 2
        rx = x1 + 46 + col * (col_w + gap)
        ry = row_top + row * row_h
        if row < 3:
            draw.line((rx, ry + row_h - 1, rx + col_w, ry + row_h - 1), fill=LIGHT, width=1)

        symbol = ascii_text(quote.symbol, "SYM")
        price = ascii_text(quote.price, "--")
        change = ascii_text(quote.change if quote.status == "OK" else "N/A", "--")
        change_w = 100
        price_w = 128
        baseline = ry + (row_h - _text_h(draw, symbol, symbol_font)) // 2 - 2
        _draw_fit(draw, (rx, baseline), symbol, symbol_font, col_w - price_w - change_w - 14)
        _draw_fit(draw, (rx + col_w - price_w - change_w, baseline + 4), price, price_font, price_w)
        _draw_fit(draw, (rx + col_w, baseline + 8), change, change_font, change_w, anchor="ra")


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


def _draw_meter(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], percent: int) -> None:
    x1, y1, x2, y2 = rect
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


def _draw_usage_card(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    name: str,
    value: str,
    status: str,
) -> None:
    x1, y1, x2, y2 = rect
    name_font = _font(24, bold=True)
    pct_font = _font(48, bold=True)
    foot_font = _font(18)
    percent = _usage_percent(value)
    draw.text((x1, y1), ascii_text(name).upper(), fill=INK, font=name_font)
    pct_text = f"{percent}%"
    draw.text((x2, y1 - 4), pct_text, fill=INK, font=pct_font, anchor="ra")
    meter_top = min(y1 + 70, y2 - 82)
    _draw_meter(draw, (x1, meter_top, x2, meter_top + 52), percent)
    _draw_fit(draw, (x1, meter_top + 66), ascii_text(_usage_note(value, status)).upper(), foot_font, x2 - x1)


def _draw_focus(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], codex: CodexUsage) -> None:
    x1, y1, x2, y2 = rect
    content_y = _section_title(draw, rect, "Codex Usage", "Current")
    gap = 46
    card_w = ((x2 - x1) - 92 - gap) // 2
    top = content_y + 20
    bottom = y2 - 12
    _draw_usage_card(draw, (x1 + 46, top, x1 + 46 + card_w, bottom), "5H Window", codex.primary, codex.status)
    _draw_usage_card(draw, (x1 + 46 + card_w + gap, top, x2 - 46, bottom), "Weekly", codex.secondary, codex.status)


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


def render_dashboard(data: DashboardData, output: str | Path) -> Path:
    image = Image.new("L", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)

    x1 = DASH_X
    y1 = DASH_Y
    x2 = DASH_X + DASH_W
    y2 = DASH_Y + DASH_H

    heights = [280, 484, 282, 378]
    top = y1
    sections: list[tuple[int, int, int, int]] = []
    for height in heights:
        sections.append((x1, top, x2, top + height))
        top += height

    for _, _, _, section_bottom in sections[:-1]:
        draw.line((x1, section_bottom, x2, section_bottom), fill=INK, width=LINE_W)

    _draw_hero(draw, sections[0], data)
    _draw_market(draw, sections[1], data.market)
    _draw_focus(draw, sections[2], data.codex)
    _draw_todos(draw, sections[3], data.todos)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path
