from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

from kindle_dash_gen.app import create_app
from kindle_dash_gen.config import DEFAULT_CONFIG, save_config, validate_config
from kindle_dash_gen.data import CodexUsage, MarketQuote, WeatherReport
from kindle_dash_gen.render import DashboardData, render_dashboard


def sample_data() -> DashboardData:
    return DashboardData(
        generated_at=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
        market=[MarketQuote("TEST", "1.00", "+0.00%")],
        weather=WeatherReport("Shanghai", "25.0 C", "Wind 3.0 km/h"),
        codex=CodexUsage("10% used", "20% used", "yes"),
    )


class FullConfigTests(unittest.TestCase):
    def test_save_config_normalizes_all_sections_and_preserves_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            config = validate_config(DEFAULT_CONFIG)
            config["server"]["port"] = "6000"
            config["market"]["symbols"] = [" AAPL ", "AAPL", "MSFT"]
            config["custom"] = {"keep": True}

            saved = save_config(path, config)

            self.assertEqual(saved["server"]["port"], 6000)
            self.assertEqual(saved["market"]["symbols"], ["AAPL", "MSFT"])
            self.assertTrue(yaml.safe_load(path.read_text(encoding="utf-8"))["custom"]["keep"])

    def test_validation_rejects_partial_coordinates_and_invalid_cron(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        config["weather"]["latitude"] = 30
        with self.assertRaisesRegex(ValueError, "set together"):
            validate_config(config)

        config = validate_config(DEFAULT_CONFIG)
        config["schedule"]["cron"] = "61 * * * *"
        with self.assertRaisesRegex(ValueError, "Invalid schedule"):
            validate_config(config)


class SettingsApiTests(unittest.TestCase):
    def test_complete_config_api_reads_and_atomically_updates_runtime_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("server:\n  port: 5678\ncodex:\n  token: secret\n", encoding="utf-8")
            client = create_app(path).test_client()

            response = client.get("/api/config")
            self.assertEqual(response.status_code, 200)
            self.assertIn("img-src 'self' blob:", response.headers["Content-Security-Policy"])
            config = response.get_json()["config"]
            self.assertEqual(config["server"]["port"], 5678)
            self.assertIn("market", config)

            config["output"]["orientation"] = "landscape"
            config["weather"] = {"location": "Hangzhou", "latitude": None, "longitude": None}
            response = client.put("/api/config", json={"config": config})
            self.assertEqual(response.status_code, 200)
            written = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(written["output"]["orientation"], "landscape")
            self.assertEqual(written["weather"]["location"], "Hangzhou")

    def test_landscape_preview_is_rotated_for_browser_without_changing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "dash.png"
            config_path = root / "config.yaml"
            render_dashboard(sample_data(), output, "landscape")
            original = output.read_bytes()
            config = validate_config(DEFAULT_CONFIG)
            config["output"] = {"path": str(output), "orientation": "landscape"}
            config["cache"]["data_path"] = str(root / "snapshot.json")
            save_config(config_path, config)
            client = create_app(config_path).test_client()

            response = client.get("/api/preview")

            self.assertEqual(response.status_code, 200)
            with Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.size, (1440, 1080))
                self.assertEqual(image.mode, "L")
            self.assertEqual(output.read_bytes(), original)

    def test_preview_endpoint_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            output = root / "dash.png"
            render_dashboard(sample_data(), output, "portrait")
            original = output.read_bytes()
            config = validate_config(DEFAULT_CONFIG)
            config["output"]["path"] = str(output)
            config["cache"]["data_path"] = str(root / "must-not-exist.json")
            save_config(config_path, config)
            client = create_app(config_path).test_client()

            self.assertEqual(client.get("/api/preview").status_code, 200)
            self.assertEqual(client.post("/api/preview", json={"config": config}).status_code, 405)
            self.assertEqual(output.read_bytes(), original)
            self.assertFalse(Path(config["cache"]["data_path"]).exists())


if __name__ == "__main__":
    unittest.main()
