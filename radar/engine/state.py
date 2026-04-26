from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque


@dataclass
class Candle:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    confirmed: bool


@dataclass
class TradeDelta:
    ts_ms: int
    delta: float


@dataclass
class OrderBookSnapshot:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    previous_ask_notional_band: float = 0.0
    ask_notional_band: float = 0.0
    bid_notional_band: float = 0.0
    updated_at: float = 0.0


@dataclass
class SymbolState:
    symbol: str
    cluster_id: str
    cluster_name: str
    price: float | None = None
    price_updated_at: float = 0.0
    change_24h_pct: float | None = None
    range_24h_pct: float | None = None
    turnover_24h: float | None = None
    candles: dict[str, Deque[Candle]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=220)))
    trade_deltas: Deque[TradeDelta] = field(default_factory=lambda: deque(maxlen=10000))
    spread_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    orderbook: OrderBookSnapshot = field(default_factory=OrderBookSnapshot)
    last_alert_at: dict[str, float] = field(default_factory=dict)
    active_signals: dict[str, dict[str, float | str]] = field(default_factory=dict)
    entry_pending: dict[str, Any] | None = None

    def update_price(self, price: float) -> None:
        self.price = price
        self.price_updated_at = time.time()

    def update_market_stats(
        self,
        change_24h_pct: float | None = None,
        range_24h_pct: float | None = None,
        turnover_24h: float | None = None,
    ) -> None:
        if change_24h_pct is not None:
            self.change_24h_pct = change_24h_pct
        if range_24h_pct is not None:
            self.range_24h_pct = range_24h_pct
        if turnover_24h is not None:
            self.turnover_24h = turnover_24h

    def upsert_candle(self, timeframe: str, candle: Candle) -> None:
        series = self.candles[timeframe]
        if series and series[-1].start_ms == candle.start_ms:
            series[-1] = candle
            return
        series.append(candle)

    def add_trade_delta(self, ts_ms: int, side: str, size: float) -> None:
        delta = size if side.lower() == "buy" else -size
        self.trade_deltas.append(TradeDelta(ts_ms=ts_ms, delta=delta))

    def add_spread_sample(self, spread_pct: float) -> None:
        self.spread_samples.append(spread_pct)

    def average_spread(self, exclude_latest: bool = True) -> float:
        samples = list(self.spread_samples)
        if exclude_latest and len(samples) > 1:
            samples = samples[:-1]
        if not samples:
            return 0.0
        return sum(samples) / len(samples)

    def cvd_since(self, window_ms: int) -> float:
        cutoff = int(time.time() * 1000) - window_ms
        while self.trade_deltas and self.trade_deltas[0].ts_ms < cutoff:
            self.trade_deltas.popleft()
        return sum(item.delta for item in self.trade_deltas if item.ts_ms >= cutoff)

    def can_alert(self, rule_id: str, cooldown_minutes: float) -> bool:
        last = self.last_alert_at.get(rule_id, 0.0)
        return (time.time() - last) >= cooldown_minutes * 60

    def mark_alert(self, rule_id: str) -> None:
        self.last_alert_at[rule_id] = time.time()

    def activate_signal(self, rule_id: str, price: float, level: str, metrics: dict[str, float | str]) -> None:
        signal = {
            "price": price,
            "level": level,
            "started_at": time.time(),
        }
        for key, value in metrics.items():
            if isinstance(value, (int, float, str)):
                signal[key] = value
        self.active_signals[rule_id] = signal

    def deactivate_signal(self, rule_id: str) -> None:
        self.active_signals.pop(rule_id, None)


class MarketState:
    def __init__(self) -> None:
        self.symbols: dict[str, SymbolState] = {}

    def register_symbol(self, symbol: str, cluster_id: str, cluster_name: str) -> SymbolState:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolState(symbol=symbol, cluster_id=cluster_id, cluster_name=cluster_name)
        return self.symbols[symbol]

    def get(self, symbol: str) -> SymbolState | None:
        return self.symbols.get(symbol)
