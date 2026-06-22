from __future__ import annotations

import argparse
import logging

from kindle_dash_gen.app import build_dashboard, create_app, start_scheduler
from kindle_dash_gen.config import load_config
from kindle_dash_gen.logbuffer import install_log_buffer


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_buffer()

    parser = argparse.ArgumentParser(description="Generate and serve a Kindle dashboard PNG.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML.")
    parser.add_argument("--once", action="store_true", help="Generate one image and exit.")
    parser.add_argument("--serve", action="store_true", help="Run the HTTP server.")
    args = parser.parse_args()

    if args.once:
        path = build_dashboard(args.config)
        print(f"Generated {path}")
        return

    config = load_config(args.config)
    server = config.get("server", {})
    app = create_app(args.config)
    print("Serving existing dashboard at /dash.png")
    start_scheduler(args.config)
    app.run(host=server.get("host") or "0.0.0.0", port=int(server.get("port") or 5678))


if __name__ == "__main__":
    main()
