from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from radar.engine.rules import Alert
from radar.engine.state import MarketState
from radar.paper import PaperPortfolio


class StatusStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("RADAR_STATUS_FILE", "storage/status.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.alerts: list[dict[str, Any]] = []
        self.workers: dict[str, dict[str, Any]] = {}
        self.initial_prices: dict[str, float] = {}
        self.previous_prices: dict[str, float] = {}
        self.paper = PaperPortfolio(
            initial_cash=float(os.getenv("PAPER_INITIAL_CASH", "1000")),
            position_fraction=float(os.getenv("PAPER_POSITION_FRACTION", "0.10")),
        )

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
        self.paper.handle_alert(alert)

    def write(self, state: MarketState) -> None:
        symbols = []
        prices: dict[str, float | None] = {}
        for item in state.symbols.values():
            previous_price = self.previous_prices.get(item.symbol)
            initial_price = self.initial_prices.get(item.symbol)
            if item.price is not None:
                if initial_price is None:
                    initial_price = item.price
                    self.initial_prices[item.symbol] = item.price
                self.previous_prices[item.symbol] = item.price
            prices[item.symbol] = item.price
            symbols.append(
                {
                    "symbol": item.symbol,
                    "cluster": item.cluster_name,
                    "price": item.price,
                    "initial_price": initial_price,
                    "previous_price": previous_price,
                    "change_pct": _pct_change(initial_price, item.price),
                    "tick_change_pct": _pct_change(previous_price, item.price),
                    "price_updated_at": item.price_updated_at,
                    "candles": {timeframe: len(candles) for timeframe, candles in item.candles.items()},
                }
            )

        payload = {
            "updated_at": time.time(),
            "workers": self.workers,
            "symbols": sorted(symbols, key=lambda row: row["symbol"]),
            "alerts": self.alerts,
            "paper": self.paper.mark_to_market(prices),
        }

        fd, temp_name = tempfile.mkstemp(prefix="status-", suffix=".json", dir=str(self.path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temp_name, self.path)


def _pct_change(start: float | None, current: float | None) -> float | None:
    if start is None or current is None or start <= 0:
        return None
    return round(100.0 * (current - start) / start, 2)
