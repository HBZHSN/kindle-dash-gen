# Project Notes

- This project generates `dash.png` for Kindle dashboards.
- The output image must remain `1080x1440`, 8-bit grayscale PNG, with no alpha channel.
- Rendered dashboard text should be English-only. Sanitize non-ASCII names before drawing.
- Runtime config is `config.yaml`; every image generation must reload it instead of caching config.
- Keep real Codex bearer tokens out of committed files. Use `config.example.yaml` only as a placeholder.
- Obsidian todo logic:
  - `1-Projects` children are open tasks.
  - `4-Archive` children are completed tasks.
  - Children may be files or folders.
  - Numeric date prefixes in task names should be stripped for display.
- Codex usage special case: if a window reports `used_percent == 1` and `reset_after_seconds == limit_window_seconds`, treat it as a not-yet-started window, not real 1% usage.
- Prefer graceful degradation. Failed yfinance, weather, Codex, or Obsidian reads should not prevent producing `/dash.png`.
- Data generation should read the previous local snapshot from `cache.data_path`, merge in fresh data, write the merged snapshot, and only then render the image. If a later fetch fails, reuse the last successful data for that section.
