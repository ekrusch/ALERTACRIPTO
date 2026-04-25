from __future__ import annotations

import time
from typing import Any

from radar.engine.rules import Alert


class PaperPortfolio:
    def __init__(self, initial_cash: float = 1000.0, position_fraction: float = 0.10) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.position_fraction = position_fraction
        self.positions: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []

    def restore(self, snapshot: dict[str, Any]) -> None:
        cash = snapshot.get("cash")
        if isinstance(cash, (int, float)):
            self.cash = float(cash)

        positions = snapshot.get("positions", [])
        if isinstance(positions, list):
            self.positions = {
                str(position["symbol"]): position
                for position in positions
                if isinstance(position, dict) and position.get("symbol")
            }

        trades = snapshot.get("trades", [])
        if isinstance(trades, list):
            self.trades = [trade for trade in trades if isinstance(trade, dict)][:2000]

    def handle_alert(self, alert: Alert) -> bool:
        if _is_exit(alert):
            return self.sell(alert.symbol, alert.price, f"saida: {alert.rule}", alert.metrics)
        return self.buy(alert.symbol, alert.price, f"entrada: {alert.rule}", alert.metrics)

    def buy(self, symbol: str, price: float, reason: str, metrics: dict[str, float | str] | None = None) -> bool:
        if price <= 0 or symbol in self.positions or self.cash <= 0:
            return False

        amount_usd = min(self.cash, self.initial_cash * self.position_fraction)
        qty = amount_usd / price
        metrics = metrics or {}
        self.cash -= amount_usd
        position = {
            "symbol": symbol,
            "qty": qty,
            "avg_price": price,
            "invested_usd": amount_usd,
            "opened_at": time.time(),
            "last_price": price,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "entry_reason": reason,
            "entry_metrics": metrics,
        }
        for key in ("peak_price", "trailing_stop_pct", "trailing_stop_price"):
            if key in metrics:
                position[key] = metrics[key]
        self.positions[symbol] = position
        self._record_trade("BUY", symbol, price, qty, amount_usd, 0.0, reason, {"entry_metrics": metrics})
        return True

    def sell(self, symbol: str, price: float, reason: str, metrics: dict[str, float | str] | None = None) -> bool:
        if price <= 0:
            return False
        position = self.positions.pop(symbol, None)
        if position is None:
            return False

        qty = float(position["qty"])
        proceeds = qty * price
        invested = float(position["invested_usd"])
        avg_price = float(position["avg_price"])
        pnl_usd = proceeds - invested
        pnl_pct = 100.0 * pnl_usd / invested if invested > 0 else 0.0
        opened_at = _as_float(position.get("opened_at"))
        closed_at = time.time()
        peak_price = _as_float(position.get("peak_price")) or max(avg_price, price)
        max_gain_pct = 100.0 * (peak_price - avg_price) / avg_price if avg_price > 0 else 0.0
        self.cash += proceeds
        self._record_trade(
            "SELL",
            symbol,
            price,
            qty,
            proceeds,
            pnl_usd,
            reason,
            {
                "entry_price": round(avg_price, 8),
                "exit_price": round(price, 8),
                "pnl_pct": round(pnl_pct, 2),
                "opened_at": opened_at,
                "closed_at": closed_at,
                "duration_seconds": round(closed_at - opened_at, 2) if opened_at is not None else None,
                "entry_reason": position.get("entry_reason"),
                "entry_metrics": position.get("entry_metrics", {}),
                "exit_metrics": metrics or {},
                "peak_price": round(peak_price, 8),
                "max_gain_pct": round(max_gain_pct, 2),
                "trailing_stop_pct": position.get("trailing_stop_pct"),
                "trailing_stop_price": position.get("trailing_stop_price"),
            },
        )
        return True

    def mark_to_market(self, prices: dict[str, float | None]) -> dict[str, Any]:
        open_value = 0.0
        open_pnl = 0.0
        for symbol, position in self.positions.items():
            price = prices.get(symbol) or float(position["last_price"])
            qty = float(position["qty"])
            invested = float(position["invested_usd"])
            value = qty * price
            pnl_usd = value - invested
            pnl_pct = 100.0 * pnl_usd / invested if invested > 0 else 0.0
            position["last_price"] = price
            position["value_usd"] = value
            position["pnl_usd"] = pnl_usd
            position["pnl_pct"] = pnl_pct
            stop_pct = _as_float(position.get("trailing_stop_pct"))
            if stop_pct is not None and stop_pct > 0:
                peak_price = max(_as_float(position.get("peak_price")) or price, price)
                position["peak_price"] = peak_price
                position["trailing_stop_price"] = peak_price * (1.0 - stop_pct / 100.0)
            open_value += value
            open_pnl += pnl_usd

        equity = self.cash + open_value
        total_pnl = equity - self.initial_cash
        return {
            "mode": "paper",
            "initial_cash": round(self.initial_cash, 2),
            "cash": round(self.cash, 2),
            "open_value": round(open_value, 2),
            "equity": round(equity, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_pnl_pct": round(100.0 * total_pnl / self.initial_cash, 2) if self.initial_cash > 0 else 0.0,
            "open_pnl_usd": round(open_pnl, 2),
            "positions": list(self.positions.values()),
            "trades": self.trades[:2000],
        }

    def _record_trade(
        self,
        side: str,
        symbol: str,
        price: float,
        qty: float,
        notional_usd: float,
        pnl_usd: float,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        extra = extra or {}
        self.trades.insert(
            0,
            {
                "ts": time.time(),
                "side": side,
                "symbol": symbol,
                "price": round(price, 8),
                "qty": round(qty, 8),
                "notional_usd": round(notional_usd, 2),
                "pnl_usd": round(pnl_usd, 2),
                "reason": reason,
                **extra,
            },
        )
        self.trades = self.trades[:2000]


def _is_exit(alert: Alert) -> bool:
    return alert.rule.endswith("_exit") or alert.metrics.get("alert_level") == "SAIDA"


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
