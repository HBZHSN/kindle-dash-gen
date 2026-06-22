from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from kindle_dash_gen import data as data_module
from kindle_dash_gen.app import _merge_market
from PIL import Image, ImageChops, ImageDraw

from kindle_dash_gen.config import DEFAULT_CONFIG, validate_config
from kindle_dash_gen.data import CodexUsage, MarketQuote, WeatherReport, _fetch_market_quote, _market_is_closed, _quote_from_intraday
from kindle_dash_gen.render import DashboardData, _draw_market_symbol, _font, render_dashboard
from kindle_dash_gen.text import parse_symbol_spec


class MarketIntradayTests(unittest.TestCase):
    def test_market_closed_state_uses_yahoo_state_and_session_end(self) -> None:
        now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

        self.assertTrue(_market_is_closed({"marketState": "CLOSED"}, now))
        self.assertTrue(_market_is_closed({"marketState": "POST"}, now))
        self.assertFalse(_market_is_closed({"marketState": "REGULAR"}, now))
        self.assertFalse(_market_is_closed({"marketState": "PRE"}, now))
        self.assertTrue(
            _market_is_closed(
                {"currentTradingPeriod": {"regular": {"end": int(now.timestamp()) - 1}}},
                now,
            )
        )
        self.assertFalse(
            _market_is_closed(
                {"currentTradingPeriod": {"regular": {"end": int(now.timestamp()) + 1}}},
                now,
            )
        )

    def test_closed_marker_is_drawn_left_without_moving_symbol(self) -> None:
        font = _font(31, bold=True)
        open_image = Image.new("L", (300, 80), 255)
        closed_image = Image.new("L", (300, 80), 255)
        _draw_market_symbol(ImageDraw.Draw(open_image), (60, 10), "TEST", font, 180, False)
        _draw_market_symbol(ImageDraw.Draw(closed_image), (60, 10), "TEST", font, 180, True)

        self.assertIsNone(ImageChops.difference(open_image.crop((60, 0, 300, 80)), closed_image.crop((60, 0, 300, 80))).getbbox())
        self.assertIsNotNone(ImageChops.difference(open_image.crop((0, 0, 60, 80)), closed_image.crop((0, 0, 60, 80))).getbbox())

    def test_quote_uses_yahoo_chart_metadata_for_price_and_previous_close(self) -> None:
        quote = _quote_from_intraday(
            "GC=F",
            pd.Series([4231.20, 4167.50]),
            {"regularMarketPrice": 4172.90, "chartPreviousClose": 4245.90},
        )

        self.assertEqual(quote.price, "4,172.90")
        self.assertEqual(quote.change, "-1.72%")
        self.assertAlmostEqual(quote.intraday[-1], -1.7193, places=4)

    def test_official_index_close_does_not_fall_back_to_zero_change(self) -> None:
        quote = _quote_from_intraday(
            "^NDX",
            pd.Series([30261.60, 30393.78]),
            {"regularMarketPrice": 30406.193, "previousClose": 29670.947},
        )

        self.assertEqual(quote.price, "30,406.19")
        self.assertEqual(quote.change, "+2.48%")
        self.assertGreater(quote.intraday[-1], 2.0)

    def test_merge_keeps_cached_curve_when_only_intraday_fetch_fails(self) -> None:
        current = [MarketQuote("TEST", "101.00", "+1.00%")]
        previous = [MarketQuote("TEST", "100.00", "+0.00%", intraday=[0.0, 0.5, 1.0])]

        merged = _merge_market(current, previous)

        self.assertEqual(merged[0].price, "101.00")
        self.assertEqual(merged[0].change, "+1.00%")
        self.assertEqual(merged[0].intraday, [0.0, 0.5, 1.0])

    def test_curve_changes_landscape_render_but_not_portrait(self) -> None:
        base = DashboardData(
            generated_at=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
            market=[MarketQuote("TEST", "101.00", "+1.00%")],
            weather=WeatherReport("Shanghai", "25.0 C", "Wind 3.0 km/h"),
            codex=CodexUsage("10% used", "20% used", "yes"),
        )
        with_curve = DashboardData(
            generated_at=base.generated_at,
            market=[MarketQuote("TEST", "101.00", "+1.00%", intraday=[0.0, 0.7, -0.2, 1.0])],
            weather=base.weather,
            codex=base.codex,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for orientation in ("portrait", "landscape"):
                plain_path = root / f"{orientation}-plain.png"
                curve_path = root / f"{orientation}-curve.png"
                render_dashboard(base, plain_path, orientation)
                render_dashboard(with_curve, curve_path, orientation)
                if orientation == "portrait":
                    self.assertEqual(plain_path.read_bytes(), curve_path.read_bytes())
                else:
                    self.assertNotEqual(plain_path.read_bytes(), curve_path.read_bytes())


class FallbackSymbolTests(unittest.TestCase):
    def test_parse_symbol_spec_splits_primary_and_fallback(self) -> None:
        self.assertEqual(parse_symbol_spec("^NDX(NQ=F)"), ("^NDX", "NQ=F"))
        self.assertEqual(parse_symbol_spec("  ^NDX ( NQ=F ) "), ("^NDX", "NQ=F"))
        self.assertEqual(parse_symbol_spec("AAPL"), ("AAPL", None))
        self.assertEqual(parse_symbol_spec("^NDX(NQ=F"), ("^NDX(NQ=F", None))

    def test_config_validation_accepts_and_normalizes_spec(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        config["market"]["symbols"] = ["^NDX( NQ=F )", "AAPL"]
        normalized = validate_config(config)
        self.assertEqual(normalized["market"]["symbols"], ["^NDX(NQ=F)", "AAPL"])

    def test_config_validation_rejects_malformed_spec(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        config["market"]["symbols"] = ["^NDX(NQ=F"]
        with self.assertRaisesRegex(ValueError, "PRIMARY\\(FALLBACK\\)"):
            validate_config(config)

    def test_open_primary_shows_primary_only(self) -> None:
        primary = MarketQuote("^NDX", "30,406.19", "+2.48%", is_closed=False)

        with mock.patch.object(data_module, "_fetch_single_quote", return_value=primary) as fetch:
            quote = _fetch_market_quote("^NDX(NQ=F)")

        fetch.assert_called_once_with("^NDX", False)
        self.assertEqual(quote.symbol, "^NDX")
        self.assertEqual(quote.secondary_price, "")

    def test_closed_primary_shows_fallback_with_primary_secondary(self) -> None:
        primary = MarketQuote("^NDX", "30,406.19", "+2.48%", is_closed=True)
        fallback = MarketQuote("NQ=F", "30,500.00", "+0.30%", is_24h=True)

        def fake_fetch(symbol: str, denoise: bool = False) -> MarketQuote:
            return primary if symbol == "^NDX" else fallback

        with mock.patch.object(data_module, "_fetch_single_quote", side_effect=fake_fetch):
            quote = _fetch_market_quote("^NDX(NQ=F)")

        self.assertEqual(quote.symbol, "NQ=F")
        self.assertEqual(quote.price, "30,500.00")
        self.assertEqual(quote.secondary_symbol, "^NDX")
        self.assertEqual(quote.secondary_price, "30,406.19")
        self.assertEqual(quote.secondary_change, "+2.48%")

    def test_secondary_line_changes_render_output(self) -> None:
        base_quote = MarketQuote("NQ=F", "30,500.00", "+0.30%")
        secondary_quote = MarketQuote(
            "NQ=F",
            "30,500.00",
            "+0.30%",
            secondary_symbol="^NDX",
            secondary_price="30,406.19",
            secondary_change="+2.48%",
        )
        weather = WeatherReport("Shanghai", "25.0 C", "Wind 3.0 km/h")
        codex = CodexUsage("10% used", "20% used", "yes")
        generated_at = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
        base = DashboardData(generated_at, [base_quote], weather, codex)
        with_secondary = DashboardData(generated_at, [secondary_quote], weather, codex)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for orientation in ("portrait", "landscape"):
                plain = root / f"{orientation}-plain.png"
                extra = root / f"{orientation}-secondary.png"
                render_dashboard(base, plain, orientation)
                render_dashboard(with_secondary, extra, orientation)
                self.assertNotEqual(plain.read_bytes(), extra.read_bytes())


if __name__ == "__main__":
    unittest.main()
