# AGENTS.md

## Cursor Cloud specific instructions

### Overview

**Radar de Anomalias Cripto** — real-time crypto monitoring + paper-trading system. Two main processes:

| Process | Command | Purpose |
|---|---|---|
| Radar engine | `source .venv/bin/activate && python -m radar.main` | Async WebSocket connections to Bybit/MEXC/KuCoin, anomaly detection, paper trading. Writes `storage/status.json`. |
| Streamlit dashboard | `source .venv/bin/activate && streamlit run app.py --server.port 8501 --server.headless true` | Web UI reading `storage/status.json`. |

Both must run simultaneously for full functionality. The radar engine must start **first** so it creates `storage/status.json` before the dashboard reads it.

### Quick reference

- **Dependencies**: `pip install -r requirements.txt` (websockets, streamlit, python-dotenv). No database; state is JSON on disk.
- **Config**: `.env` (copy from `.env.example`), `config/clusters.json`, `config/radar_thresholds.json`, `config/variance_scanner.json`.
- **Lint/Test**: No linter or test framework is configured in this repo. Validate via `python -c "import radar"` and running both services.
- **External APIs**: Bybit, MEXC, KuCoin — all public, no API keys needed. Telegram/Discord alerts are optional (need tokens in `.env`).

### Gotchas

- `python3.12-venv` system package is required but may not be pre-installed. Install with `sudo apt-get install -y python3.12-venv` if `python3 -m venv` fails.
- The `storage/` directory must exist before the radar engine runs. Create with `mkdir -p storage`.
- The radar engine takes ~10-15 seconds to start writing `storage/status.json` after launch. If the Streamlit dashboard shows "Nao encontrou status", wait for the engine to produce the file.
- The Streamlit dashboard auto-refreshes every 10 seconds via `@st.fragment(run_every="10s")`.
- For the workspace rule about deploying to Hetzner (`root@178.104.166.222`): this only applies when changes affect `radar/`, `config/`, or `scripts/` that run on the server. The cloud agent environment does not have SSH access to the Hetzner server.
