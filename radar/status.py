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

    def record_alert(self, alert: Alert) -> bool:
        paper_changed = self.paper.handle_alert(alert)

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
        return paper_changed

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
                    "change_24h_pct": _round_optional(item.change_24h_pct),
                    "range_24h_pct": _round_optional(item.range_24h_pct),
                    "turnover_24h": _round_optional(item.turnover_24h, 2),
                    "opportunity_score": _opportunity_score(item),
                    "active_signal": _active_signal(item.active_signals),
                    "price_updated_at": item.price_updated_at,
                    "candles": {timeframe: len(candles) for timeframe, candles in item.candles.items()},
                }
            )

        opportunities = [
            item
            for item in symbols
            if item.get("opportunity_score") is not None and item.get("price") is not None
        ]
        opportunities = sorted(opportunities, key=lambda row: row.get("opportunity_score") or 0, reverse=True)[:30]

        payload = {
            "updated_at": time.time(),
            "workers": self.workers,
            "symbols": sorted(symbols, key=lambda row: row["symbol"]),
            "opportunities": opportunities,
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


def _is_exit(alert: Alert) -> bool:
    return alert.rule.endswith("_exit") or alert.metrics.get("alert_level") == "SAIDA"


def _active_signal(signals: dict[str, dict[str, float | str]]) -> dict[str, float | str] | None:
    if not signals:
        return None
    rule, signal = next(iter(signals.items()))
    fields = {
        "rule": rule,
        "price": signal.get("price"),
        "level": signal.get("level"),
        "started_at": signal.get("started_at"),
        "peak_price": signal.get("peak_price"),
        "trailing_stop_pct": signal.get("trailing_stop_pct"),
        "trailing_stop_price": signal.get("trailing_stop_price"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _round_optional(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _opportunity_score(item: Any) -> float | None:
    turnover = item.turnover_24h
    change = item.change_24h_pct
    range_pct = item.range_24h_pct
    if turnover is None and change is None and range_pct is None:
        return None

    score = 0.0
    if turnover is not None:
        if turnover >= 20_000_000:
            score += 35
        elif turnover >= 5_000_000:
            score += 28
        elif turnover >= 1_000_000:
            score += 18
        elif turnover >= 250_000:
            score += 8
    if change is not None:
        if change > 0:
            score += min(35, change * 1.8)
        else:
            score += max(-20, change)
    if range_pct is not None:
        if 4 <= range_pct <= 25:
            score += 18
        elif range_pct > 45:
            score -= 15
        elif range_pct <= 3:
            score += 6
    return round(max(0.0, min(score, 100.0)), 1)
