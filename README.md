# kindle-dash-gen

Generate a Kindle-friendly dashboard PNG and serve it at `/dash.png`.

The generated image is:

- `1080x1440`
- 8-bit grayscale PNG
- No alpha channel
- English-only text, with non-ASCII names sanitized before rendering

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
```

Edit `config.yaml` before running:

- `market.symbols`: yfinance symbols to display
- `obsidian.path`: Obsidian vault path
- `obsidian.projects_dir`: open task folder, usually `1-Projects`
- `obsidian.archive_dir`: completed task folder, usually `4-Archive`
- `weather.location`: city name, or set `latitude` and `longitude`
- `codex.token`: ChatGPT bearer token for Codex usage
- `schedule.cron`: render schedule, for example `*/15 * * * *`
- `cache.data_path`: local data snapshot used when a later fetch fails

Do not commit a real `codex.token`. Keep it in local `config.yaml`; `config.example.yaml` must stay as a placeholder.

## Generate Once

```powershell
python dash.py --once
```

This writes `dash.png` or the path configured in `output.path`.

## Run Server

```powershell
python dash.py --serve
```

The server returns the existing image from disk and refreshes it in the background using `schedule.cron`.
HTTP requests to `/dash.png` only return the already generated file, so the Kindle does not wait for market,
weather, Codex, or Obsidian reads.

The default URL is:

```text
http://<your-lan-ip>:5678/dash.png
```

For example:

```text
http://192.168.31.115:5678/dash.png
```

Open the settings and live preview page at:

```text
http://<your-lan-ip>:5678/settings
```

The page edits every runtime setting (output, data sources, schedule, server, and Codex token) and saves the
complete configuration atomically to `config.yaml`. The live preview reloads the existing `dash.png` every
60 seconds without triggering data fetches or image generation. Landscape Kindle output is automatically
rotated back to a browser-friendly 1440x1080 preview. A clock appears beside `5H` in both
portrait and landscape dashboards when the token expires within 24 hours or is already expired. The settings
page exposes a credential, so keep the server on a trusted LAN and do not publish it to the internet.

`/dash.png` reads `config.yaml` only to locate `output.path`, then returns that existing PNG. If it has not
been generated yet, run `python dash.py --once` first.

## Data Behavior

- Market data uses `yfinance`.
- Market quotes are rendered as text in two columns, up to 16 symbols.
- Weather uses Open-Meteo and does not require an API key.
- Codex usage calls `https://chatgpt.com/backend-api/wham/usage` with `codex.token`.
- If Codex returns `used_percent: 1` and `reset_after_seconds` equals `limit_window_seconds`, the primary window is displayed as `0% not started`.
- Todo data is read from the Obsidian vault:
  - Children under `1-Projects` are open tasks.
  - Children under `4-Archive` are completed tasks.
  - Files and folders are both supported.

Before rendering, the app writes the merged dashboard data to `cache.data_path`. Later failures reuse the last successful data where possible, so the endpoint still returns a valid PNG with the most recent usable values.
