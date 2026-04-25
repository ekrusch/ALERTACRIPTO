from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable

import websockets

from radar.config import ClusterConfig
from radar.engine.rules import Alert, evaluate_symbol
from radar.engine.state import Candle, MarketState, SymbolState
from radar.status import StatusStore


AlertCallback = Callable[[Alert], Awaitable[None]]


class BybitClusterWorker:
    def __init__(
        self,
        websocket_url: str,
        cluster: ClusterConfig,
        market_state: MarketState,
        status_store: StatusStore,
        on_alert: AlertCallback,
    ) -> None:
        self.websocket_url = websocket_url
        self.cluster = cluster
        self.market_state = market_state
        self.status_store = status_store
        self.on_alert = on_alert
        self.worker_id = f"bybit:{cluster.id}"

        for symbol in cluster.symbols:
            self.market_state.register_symbol(symbol, cluster.id, cluster.name)

    async def run_forever(self) -> None:
        backoff = 2
        while True:
            try:
                await self._run_once()
                backoff = 2
            except Exception as exc:
                print(f"[{self.worker_id}] websocket caiu: {exc}. Reconectando em {backoff}s.", flush=True)
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

    async def _run_once(self) -> None:
        topics = self._build_topics()
        await asyncio.to_thread(self._preload_history)
        async with websockets.connect(self.websocket_url, ping_interval=20, ping_timeout=10) as websocket:
            await websocket.send(json.dumps({"op": "subscribe", "args": topics}))
            print(f"[{self.worker_id}] monitorando {', '.join(self.cluster.symbols)}", flush=True)
            self.status_store.update_worker(
                self.worker_id,
                {
                    "status": "connected",
                    "cluster": self.cluster.name,
                    "symbols": self.cluster.symbols,
                    "topics": topics,
                },
            )
            self.status_store.write(self.market_state)

            async for raw_message in websocket:
                message = json.loads(raw_message)
                await self._handle_message(message)

    def _build_topics(self) -> list[str]:
        topics: list[str] = []
        for symbol in self.cluster.symbols:
            topics.append(f"tickers.{symbol}")
            if self.cluster.rule == "cvd_rvol_compression":
                topics.append(f"publicTrade.{symbol}")
            if self.cluster.rule == "orderbook_imbalance_vwap":
                topics.append(f"orderbook.50.{symbol}")
            for timeframe in self.cluster.timeframes:
                topics.append(f"kline.{timeframe}.{symbol}")
        return topics

    def _preload_history(self) -> None:
        for symbol in self.cluster.symbols:
            state = self.market_state.get(symbol)
            if state is None:
                continue
            for timeframe in self.cluster.timeframes:
                for candle in _fetch_bybit_klines(symbol, timeframe):
                    state.upsert_candle(timeframe, candle)
                    state.update_price(candle.close)

    async def _handle_message(self, message: dict) -> None:
        topic = message.get("topic", "")
        data = message.get("data")
        if not topic or data is None:
            return

        if topic.startswith("tickers."):
            self._handle_ticker(topic, data)
        elif topic.startswith("kline."):
            self._handle_kline(topic, data)
        elif topic.startswith("publicTrade."):
            self._handle_trade(topic, data)
        elif topic.startswith("orderbook."):
            self._handle_orderbook(topic, message)

        symbol = topic.split(".")[-1]
        state = self.market_state.get(symbol)
        if state is not None:
            alert = evaluate_symbol(state, self.cluster)
            if alert is not None:
                should_notify = self.status_store.record_alert(alert)
                self.status_store.write(self.market_state)
                if should_notify:
                    await self.on_alert(alert)

    def _handle_ticker(self, topic: str, data: dict) -> None:
        symbol = topic.split(".")[-1]
        state = self.market_state.get(symbol)
        if state is None:
            return
        last_price = data.get("lastPrice")
        if last_price is not None:
            state.update_price(float(last_price))

    def _handle_kline(self, topic: str, data: list[dict]) -> None:
        parts = topic.split(".")
        timeframe = parts[1]
        symbol = parts[2]
        state = self.market_state.get(symbol)
        if state is None:
            return

        for item in data:
            candle = Candle(
                start_ms=int(item["start"]),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item["volume"]),
                confirmed=bool(item.get("confirm", False)),
            )
            state.upsert_candle(timeframe, candle)
            state.update_price(candle.close)

    def _handle_trade(self, topic: str, data: list[dict]) -> None:
        symbol = topic.split(".")[-1]
        state = self.market_state.get(symbol)
        if state is None:
            return

        for item in data:
            state.add_trade_delta(ts_ms=int(item.get("T", time.time() * 1000)), side=item["S"], size=float(item["v"]))
            state.update_price(float(item["p"]))

    def _handle_orderbook(self, topic: str, message: dict) -> None:
        symbol = topic.split(".")[-1]
        state = self.market_state.get(symbol)
        if state is None:
            return

        data = message.get("data", {})
        orderbook = state.orderbook
        if message.get("type") == "snapshot":
            orderbook.bids.clear()
            orderbook.asks.clear()

        for price, size in data.get("b", []):
            _update_level(orderbook.bids, float(price), float(size))
        for price, size in data.get("a", []):
            _update_level(orderbook.asks, float(price), float(size))

        if state.price is None and orderbook.bids and orderbook.asks:
            best_bid = max(orderbook.bids)
            best_ask = min(orderbook.asks)
            state.update_price((best_bid + best_ask) / 2.0)

        if state.price is not None:
            band_pct = float(self.cluster.settings.get("book_band_pct", 2.0))
            bid_notional, ask_notional = _book_notional_in_band(state, band_pct)
            orderbook.previous_ask_notional_band = orderbook.ask_notional_band
            orderbook.bid_notional_band = bid_notional
            orderbook.ask_notional_band = ask_notional
            orderbook.updated_at = time.time()

    def snapshot_prices(self) -> dict[str, float | None]:
        return {symbol: self.market_state.get(symbol).price if self.market_state.get(symbol) else None for symbol in self.cluster.symbols}


def _update_level(levels: dict[float, float], price: float, size: float) -> None:
    if size == 0:
        levels.pop(price, None)
    else:
        levels[price] = size


def _book_notional_in_band(state: SymbolState, band_pct: float) -> tuple[float, float]:
    if state.price is None:
        return 0.0, 0.0
    lower = state.price * (1.0 - band_pct / 100.0)
    upper = state.price * (1.0 + band_pct / 100.0)
    bid_notional = sum(price * size for price, size in state.orderbook.bids.items() if lower <= price <= state.price)
    ask_notional = sum(price * size for price, size in state.orderbook.asks.items() if state.price <= price <= upper)
    return bid_notional, ask_notional


def _fetch_bybit_klines(symbol: str, interval: str, limit: int = 200) -> list[Candle]:
    params = urllib.parse.urlencode(
        {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
    )
    url = f"https://api.bybit.com/v5/market/kline?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[bybit:history] falha ao carregar {symbol} {interval}: {exc}", flush=True)
        return []

    if payload.get("retCode") != 0:
        print(f"[bybit:history] {symbol} {interval}: {payload.get('retMsg')}", flush=True)
        return []

    rows = payload.get("result", {}).get("list", [])
    candles = [
        Candle(
            start_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            confirmed=True,
        )
        for row in rows
    ]
    return sorted(candles, key=lambda candle: candle.start_ms)
