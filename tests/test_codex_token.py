from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

from kindle_dash_gen.app import create_app
from kindle_dash_gen.codex_token import inspect_token, normalize_token
from kindle_dash_gen.config import save_codex_token
from kindle_dash_gen.data import CodexUsage, MarketQuote, WeatherReport
from kindle_dash_gen.render import DashboardData, render_dashboard


def jwt(exp: int, iat: int | None = None) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    payload: dict[str, object] = {"exp": exp}
    if iat is not None:
        payload["iat"] = iat
    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature"


class TokenInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
        self.now_ts = int(self.now.timestamp())

    def test_normalizes_bearer_prefix(self) -> None:
        self.assertEqual(normalize_token("  Bearer abc.def.ghi  "), "abc.def.ghi")

    def test_rejects_internal_whitespace(self) -> None:
        with self.assertRaises(ValueError):
            normalize_token("abc def")

    def test_expiry_status_boundaries(self) -> None:
        self.assertEqual(inspect_token(jwt(self.now_ts + 86_401), self.now)["status"], "valid")
        self.assertEqual(inspect_token(jwt(self.now_ts + 86_400), self.now)["status"], "expiring")
        self.assertEqual(inspect_token(jwt(self.now_ts), self.now)["status"], "expired")

    def test_opaque_token_has_unknown_expiry(self) -> None:
        info = inspect_token("opaque-token", self.now)
        self.assertTrue(info["configured"])
        self.assertEqual(info["status"], "unknown")


class ConfigAndApiTests(unittest.TestCase):
    def test_save_updates_only_codex_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("server:\n  port: 9876\ncodex:\n  token: old\n  timeout_seconds: 9\n", encoding="utf-8")
            save_codex_token(path, "Bearer new-token")
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["codex"]["token"], "new-token")
            self.assertEqual(saved["codex"]["timeout_seconds"], 9)
            self.assertEqual(saved["server"]["port"], 9876)

    def test_settings_api_reads_and_saves_token_without_caching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("codex:\n  token: old-token\n", encoding="utf-8")
            client = create_app(path).test_client()

            page = client.get("/settings")
            self.assertEqual(page.status_code, 200)
            self.assertIn("no-store", page.headers["Cache-Control"])

            response = client.get("/api/codex-token")
            self.assertEqual(response.get_json()["token"], "old-token")
            self.assertIn("no-store", response.headers["Cache-Control"])

            new_token = jwt(2_000_000_000)
            response = client.put("/api/codex-token", json={"token": f"Bearer {new_token}"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["token"], new_token)
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["codex"]["token"], new_token)


class RenderContractTests(unittest.TestCase):
    def test_both_orientations_remain_kindle_pngs_with_expiry_warning(self) -> None:
        data = DashboardData(
            generated_at=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
            market=[MarketQuote("TEST", "1.00", "+0.00%")],
            weather=WeatherReport("Shanghai", "25.0 C", "Wind 3.0 km/h"),
            codex=CodexUsage("10% used", "20% used", "yes", token_expiring_soon=True),
        )
        with tempfile.TemporaryDirectory() as directory:
            for orientation in ("portrait", "landscape"):
                path = Path(directory) / f"{orientation}.png"
                render_dashboard(data, path, orientation)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1080, 1440))
                    self.assertEqual(image.mode, "L")
                    self.assertNotIn("transparency", image.info)


if __name__ == "__main__":
    unittest.main()
