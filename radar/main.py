from __future__ import annotations

import asyncio
import os
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
    while True:
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


if __name__ == "__main__":
    asyncio.run(main())
