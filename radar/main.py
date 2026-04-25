from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from typing import Any
from datetime import datetime

from dotenv import load_dotenv

from radar.config import load_config
from radar.connectors.bybit import BybitClusterWorker
from radar.connectors.kucoin import KuCoinSpotClusterWorker
from radar.connectors.mexc import MexcSpotPollingWorker
from radar.engine.state import MarketState
from radar.notifications import Notifier
from radar.status import StatusStore


async def main() -> None:
    load_dotenv()
    config = load_config()
    market_state = MarketState()
    status_store = StatusStore()
    notifier = Notifier()

    workers: list[Any] = []
    for cluster in config.clusters:
        if cluster.exchange == "bybit_linear":
            exchange = config.exchanges[cluster.exchange]
            workers.append(
                BybitClusterWorker(
                    websocket_url=exchange["websocket_url"],
                    cluster=cluster,
                    market_state=market_state,
                    status_store=status_store,
                    on_alert=notifier.send,
                )
            )
        elif cluster.exchange == "kucoin_spot":
            workers.append(
                KuCoinSpotClusterWorker(
                    cluster=cluster,
                    market_state=market_state,
                    status_store=status_store,
                    on_alert=notifier.send,
                )
            )
        elif cluster.exchange == "mexc_spot":
            workers.append(
                MexcSpotPollingWorker(
                    cluster=cluster,
                    market_state=market_state,
                    status_store=status_store,
                    on_alert=notifier.send,
                )
            )

    tasks = [asyncio.create_task(worker.run_forever()) for worker in workers]
    tasks.append(asyncio.create_task(_status_loop(workers, market_state, status_store)))
    await asyncio.gather(*tasks)


async def _status_loop(workers: list[Any], market_state: MarketState, status_store: StatusStore) -> None:
    interval = int(os.getenv("RADAR_STATUS_INTERVAL_SECONDS", "15"))
    ticker_refresh_seconds = int(os.getenv("BYBIT_TICKER_REFRESH_SECONDS", "60"))
    last_ticker_refresh = 0.0
    while True:
        now = time.time()
        if now - last_ticker_refresh >= ticker_refresh_seconds:
            await asyncio.to_thread(_refresh_bybit_market_stats, market_state)
            last_ticker_refresh = now

        for worker in workers:
            prices = worker.snapshot_prices()
            status_store.update_worker(
                worker.worker_id,
                {
                    "status": "running",
                    "cluster": worker.cluster.name,
                    "symbols": worker.cluster.symbols,
                    "prices": prices,
                },
            )
            price_text = ", ".join(f"{symbol}={_fmt_price(price)}" for symbol, price in prices.items())
            print(f"[{datetime.now().isoformat(timespec='seconds')}] PROCESSO {worker.worker_id}: {price_text}", flush=True)

        status_store.write(market_state)
        await asyncio.sleep(interval)


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "aguardando"
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}"


def _refresh_bybit_market_stats(market_state: MarketState) -> None:
    request = urllib.request.Request(
        "https://api.bybit.com/v5/market/tickers?category=linear",
        headers={"User-Agent": "radar-cripto/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[bybit:ticker-refresh] falha ao carregar tickers: {exc}", flush=True)
        return

    if payload.get("retCode") != 0:
        print(f"[bybit:ticker-refresh] {payload.get('retMsg')}", flush=True)
        return

    for item in payload.get("result", {}).get("list", []):
        symbol = item.get("symbol")
        state = market_state.get(symbol) if symbol else None
        if state is None:
            continue

        last_price = _safe_float(item.get("lastPrice"))
        if last_price is not None:
            state.update_price(last_price)
        state.update_market_stats(
            change_24h_pct=_safe_float(item.get("price24hPcnt"), multiplier=100.0),
            range_24h_pct=_range_24h_pct(item),
            turnover_24h=_safe_float(item.get("turnover24h")),
        )


def _safe_float(value: object, multiplier: float = 1.0) -> float | None:
    try:
        if value is None:
            return None
        return float(value) * multiplier
    except (TypeError, ValueError):
        return None


def _range_24h_pct(data: dict) -> float | None:
    last_price = _safe_float(data.get("lastPrice"))
    high = _safe_float(data.get("highPrice24h"))
    low = _safe_float(data.get("lowPrice24h"))
    if last_price is None or high is None or low is None or last_price <= 0 or high <= 0 or low <= 0:
        return None
    return 100.0 * (high - low) / last_price


if __name__ == "__main__":
    asyncio.run(main())
