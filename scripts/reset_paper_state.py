#!/usr/bin/env python3
"""
Zera o feedback do paper no status.json (e opcionalmente arquiva o audit JSONL),
sem apagar o restante do status (workers/symbols deixam de ser fidedignos até o
próximo tick — use com o serviço parado).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _status_path() -> Path:
    env = os.getenv("RADAR_STATUS_FILE")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    return ROOT / "storage" / "status.json"


def main() -> None:
    status_path = _status_path()

    initial_cash = float(os.getenv("PAPER_INITIAL_CASH", "1000"))

    data: dict = {}
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}

    prev = data.get("paper", {})
    if isinstance(prev, dict) and isinstance(prev.get("initial_cash"), (int, float)):
        initial_cash = float(prev["initial_cash"])

    data["paper"] = {
        "mode": "paper",
        "initial_cash": round(initial_cash, 2),
        "cash": round(initial_cash, 2),
        "open_value": 0.0,
        "equity": round(initial_cash, 2),
        "total_pnl_usd": 0.0,
        "total_pnl_pct": 0.0,
        "open_pnl_usd": 0.0,
        "positions": [],
        "trades": [],
    }
    data["paper_history"] = []
    data["alerts"] = []
    data["updated_at"] = time.time()

    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"paper zerado: {status_path}")


if __name__ == "__main__":
    main()
