from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from radar.engine.rules import Alert
from radar.engine.state import MarketState


class StatusStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("RADAR_STATUS_FILE", "storage/status.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.alerts: list[dict[str, Any]] = []
        self.workers: dict[str, dict[str, Any]] = {}

    def update_worker(self, worker_id: str, data: dict[str, Any]) -> None:
        self.workers[worker_id] = {"updated_at": time.time(), **data}

    def record_alert(self, alert: Alert) -> None:
        payload = {
            "ts": time.time(),
            "symbol": alert.symbol,
            "cluster": alert.cluster_name,
            "rule": alert.rule,
            "price": alert.price,
            "title": alert.title,
            "message": alert.message,
            "metrics": alert.metrics,
        }
        self.alerts.insert(0, payload)
        self.alerts = self.alerts[:100]

    def write(self, state: MarketState) -> None:
        symbols = []
        for item in state.symbols.values():
            symbols.append(
                {
                    "symbol": item.symbol,
                    "cluster": item.cluster_name,
                    "price": item.price,
                    "price_updated_at": item.price_updated_at,
                    "candles": {timeframe: len(candles) for timeframe, candles in item.candles.items()},
                }
            )

        payload = {
            "updated_at": time.time(),
            "workers": self.workers,
            "symbols": sorted(symbols, key=lambda row: row["symbol"]),
            "alerts": self.alerts,
        }

        fd, temp_name = tempfile.mkstemp(prefix="status-", suffix=".json", dir=str(self.path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temp_name, self.path)
