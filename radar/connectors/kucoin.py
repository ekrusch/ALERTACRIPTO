from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Awaitable, Callable

import websockets

from radar.config import ClusterConfig
from radar.engine.rules import Alert, evaluate_symbol
from radar.engine.state import Candle, MarketState
from radar.status import StatusStore


AlertCallback = Callable[[Alert], Awaitable[None]]

INTERVAL_MAP = {
    "15": "15min",
    "60": "1hour",
    "240": "4hour",
    "D": "1day",
}


class KuCoinSpotClusterWorker:
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
        self.worker_id = f"kucoin:{cluster.id}"

        for symbol in cluster.symbols:
            self.market_state.register_symbol(symbol, cluster.id, cluster.name)

    async def run_forever(self) -> None:
        backoff = 2
        while True:
            try:
                await asyncio.to_thread(self._preload_history)
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
        endpoint, token = _get_ws_endpoint()
        url = f"{endpoint}?token={token}&connectId={uuid.uuid4().hex}"
        async with websockets.connect(url, ping_interval=18, ping_timeout=10) as websocket:
            topics = self._topics()
            for topic in topics:
                await websocket.send(
                    json.dumps(
                        {
                            "id": str(int(time.time() * 1000)),
                            "type": "subscribe",
                            "topic": topic,
                            "privateChannel": False,
                            "response": True,
                        }
                    )
                )

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

    def _topics(self) -> list[str]:
        market_symbols = ",".join(self.cluster.symbols)
        topics = [f"/market/ticker:{market_symbols}"]
        for symbol in self.cluster.symbols:
            for timeframe in self.cluster.timeframes:
                interval = INTERVAL_MAP.get(timeframe)
                if interval:
                    topics.append(f"/market/candles:{symbol}_{interval}")
        return topics

    def _preload_history(self) -> None:
        for symbol in self.cluster.symbols:
            state = self.market_state.get(symbol)
            if state is None:
                continue
            for timeframe in self.cluster.timeframes:
                for candle in _fetch_kucoin_candles(symbol, timeframe):
                    state.upsert_candle(timeframe, candle)
                    state.update_price(candle.close)

    async def _handle_message(self, message: dict) -> None:
        if message.get("type") in {"welcome", "ack"}:
            return

        topic = message.get("topic", "")
        data = message.get("data", {})
        symbol = ""

        if topic.startswith("/market/ticker:"):
            symbol = data.get("symbol", "")
            state = self.market_state.get(symbol)
            price = data.get("price") or data.get("bestAsk") or data.get("bestBid")
            if state is not None and price is not None:
                state.update_price(float(price))

        elif topic.startswith("/market/candles:"):
            raw = topic.split(":", 1)[1]
            symbol, interval = raw.rsplit("_", 1)
            timeframe = _timeframe_from_interval(interval)
            candles = data.get("candles", [])
            state = self.market_state.get(symbol)
            if state is not None and timeframe and len(candles) >= 6:
                candle = _kucoin_candle_from_row(candles, timeframe)
                state.upsert_candle(timeframe, candle)
                state.update_price(candle.close)

        if symbol:
            state = self.market_state.get(symbol)
            if state is not None:
                alert = evaluate_symbol(state, self.cluster)
                if alert is not None:
                    self.status_store.record_alert(alert)
                    self.status_store.write(self.market_state)
                    await self.on_alert(alert)

    def snapshot_prices(self) -> dict[str, float | None]:
        return {symbol: self.market_state.get(symbol).price if self.market_state.get(symbol) else None for symbol in self.cluster.symbols}


def _get_ws_endpoint() -> tuple[str, str]:
    request = urllib.request.Request("https://api.kucoin.com/api/v1/bullet-public", method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "200000":
        raise RuntimeError(f"KuCoin bullet-public falhou: {payload}")
    data = payload["data"]
    return data["instanceServers"][0]["endpoint"], data["token"]


def _fetch_kucoin_candles(symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
    interval = INTERVAL_MAP.get(timeframe)
    if not interval:
        return []

    seconds = _interval_seconds(timeframe)
    end_at = int(time.time())
    start_at = end_at - seconds * limit
    params = urllib.parse.urlencode({"type": interval, "symbol": symbol, "startAt": start_at, "endAt": end_at})
    url = f"https://api.kucoin.com/api/v1/market/candles?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "radar-cripto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[kucoin:history] falha ao carregar {symbol} {timeframe}: {exc}", flush=True)
        return []

    if payload.get("code") != "200000":
        print(f"[kucoin:history] {symbol} {timeframe}: {payload}", flush=True)
        return []

    candles = [_kucoin_candle_from_row(row, timeframe) for row in payload.get("data", [])]
    return sorted(candles, key=lambda candle: candle.start_ms)


def _kucoin_candle_from_row(row: list[str], timeframe: str) -> Candle:
    start_ms = int(float(row[0])) * 1000
    return Candle(
        start_ms=start_ms,
        open=float(row[1]),
        close=float(row[2]),
        high=float(row[3]),
        low=float(row[4]),
        volume=float(row[5]),
        confirmed=start_ms + _interval_seconds(timeframe) * 1000 <= int(time.time() * 1000),
    )


def _timeframe_from_interval(interval: str) -> str | None:
    for timeframe, mapped in INTERVAL_MAP.items():
        if mapped == interval:
            return timeframe
    return None


def _interval_seconds(timeframe: str) -> int:
    if timeframe == "D":
        return 24 * 60 * 60
    return int(timeframe) * 60
