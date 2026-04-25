from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable

from radar.config import ClusterConfig
from radar.engine.rules import Alert, evaluate_symbol
from radar.engine.state import Candle, MarketState, SymbolState
from radar.status import StatusStore


AlertCallback = Callable[[Alert], Awaitable[None]]

INTERVAL_MAP = {
    "15": "15m",
    "60": "60m",
    "240": "4h",
    "D": "1d",
}


class MexcSpotPollingWorker:
    def __init__(
        self,
        cluster: ClusterConfig,
        market_state: MarketState,
        status_store: StatusStore,
        on_alert: AlertCallback,
    ) -> None:
        self.cluster = cluster
        self.market_state = market_state
        self.status_store = status_store
        self.on_alert = on_alert
        self.worker_id = f"mexc:{cluster.id}"
        self.poll_seconds = int(cluster.settings.get("poll_seconds", 60))

        for symbol in cluster.symbols:
            self.market_state.register_symbol(symbol, cluster.id, cluster.name)

    async def run_forever(self) -> None:
        backoff = 2
        while True:
            try:
                await asyncio.to_thread(self._poll_once)
                self.status_store.update_worker(
                    self.worker_id,
                    {
                        "status": "polling",
                        "cluster": self.cluster.name,
                        "symbols": self.cluster.symbols,
                        "poll_seconds": self.poll_seconds,
                    },
                )
                self.status_store.write(self.market_state)
                backoff = 2
                await asyncio.sleep(self.poll_seconds)
            except Exception as exc:
                print(f"[{self.worker_id}] polling falhou: {exc}. Tentando em {backoff}s.", flush=True)
                self.status_store.update_worker(
                    self.worker_id,
                    {
                        "status": "reconnecting",
                        "cluster": self.cluster.name,
                        "symbols": self.cluster.symbols,
                        "last_error": str(exc),
                    },
                )
                self.status_store.write(self.market_state)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _poll_once(self) -> None:
        for symbol in self.cluster.symbols:
            state = self.market_state.get(symbol)
            if state is None:
                continue

            price = _fetch_mexc_price(symbol)
            if price is not None:
                state.update_price(price)

            _update_mexc_book_state(state)
            _update_mexc_trade_state(state)

            for timeframe in self.cluster.timeframes:
                for candle in _fetch_mexc_klines(symbol, timeframe):
                    state.upsert_candle(timeframe, candle)
                    state.update_price(candle.close)

            alert = evaluate_symbol(state, self.cluster)
            if alert is not None:
                self.status_store.record_alert(alert)
                self.status_store.write(self.market_state)
                asyncio.run(self.on_alert(alert))

    def snapshot_prices(self) -> dict[str, float | None]:
        return {symbol: self.market_state.get(symbol).price if self.market_state.get(symbol) else None for symbol in self.cluster.symbols}


def _fetch_mexc_price(symbol: str) -> float | None:
    params = urllib.parse.urlencode({"symbol": symbol})
    url = f"https://api.mexc.com/api/v3/ticker/price?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[mexc:price] falha ao carregar {symbol}: {exc}", flush=True)
        return None
    price = payload.get("price")
    return float(price) if price is not None else None


def _update_mexc_book_state(state: SymbolState) -> None:
    params = urllib.parse.urlencode({"symbol": state.symbol})
    url = f"https://api.mexc.com/api/v3/ticker/bookTicker?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[mexc:book] falha ao carregar {state.symbol}: {exc}", flush=True)
        return

    bid_price = float(payload.get("bidPrice", 0) or 0)
    bid_qty = float(payload.get("bidQty", 0) or 0)
    ask_price = float(payload.get("askPrice", 0) or 0)
    ask_qty = float(payload.get("askQty", 0) or 0)
    if bid_price <= 0 or ask_price <= 0:
        return

    state.orderbook.bids = {bid_price: bid_qty}
    state.orderbook.asks = {ask_price: ask_qty}
    state.orderbook.updated_at = time.time()
    mid = (bid_price + ask_price) / 2.0
    state.add_spread_sample(100.0 * (ask_price - bid_price) / mid)


def _update_mexc_trade_state(state: SymbolState, limit: int = 100) -> None:
    params = urllib.parse.urlencode({"symbol": state.symbol, "limit": limit})
    url = f"https://api.mexc.com/api/v3/trades?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[mexc:trades] falha ao carregar {state.symbol}: {exc}", flush=True)
        return

    seen_times = {item.ts_ms for item in state.trade_deltas}
    for row in rows:
        ts_ms = int(row.get("time", time.time() * 1000))
        if ts_ms in seen_times:
            continue
        qty = float(row.get("qty", 0) or 0)
        # isBuyerMaker=true significa agressao vendedora; false significa compra a mercado.
        side = "sell" if row.get("isBuyerMaker") else "buy"
        state.add_trade_delta(ts_ms=ts_ms, side=side, size=qty)


def _fetch_mexc_klines(symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
    interval = INTERVAL_MAP.get(timeframe)
    if not interval:
        return []

    params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    url = f"https://api.mexc.com/api/v3/klines?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[mexc:history] falha ao carregar {symbol} {timeframe}: {exc}", flush=True)
        return []

    candles = [
        Candle(
            start_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            confirmed=_is_closed_candle(int(row[0]), timeframe),
        )
        for row in rows
    ]
    return sorted(candles, key=lambda candle: candle.start_ms)


def _is_closed_candle(start_ms: int, timeframe: str) -> bool:
    return start_ms + _interval_ms(timeframe) <= int(time.time() * 1000)


def _interval_ms(timeframe: str) -> int:
    if timeframe == "D":
        return 24 * 60 * 60 * 1000
    return int(timeframe) * 60 * 1000
