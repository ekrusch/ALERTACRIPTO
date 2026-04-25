from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st


STATUS_FILE = Path("storage/status.json")


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "aguardando"
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}"


def _fmt_timestamp(value: float | None) -> str:
    if not value:
        return "aguardando"
    return datetime.fromtimestamp(value).strftime("%H:%M:%S")


st.set_page_config(page_title="Radar de Anomalias Cripto", layout="wide")
st.title("Radar de Anomalias Cripto")

if not STATUS_FILE.exists():
    st.warning("O radar ainda nao gerou status. Rode: python -m radar.main")
    st.stop()

with STATUS_FILE.open("r", encoding="utf-8") as file:
    status = json.load(file)

updated_at = status.get("updated_at")
if updated_at:
    st.caption(f"Ultima atualizacao: {datetime.fromtimestamp(updated_at).strftime('%Y-%m-%d %H:%M:%S')}")

st.subheader("Processos / Workers")
workers = status.get("workers", {})
if workers:
    worker_rows = []
    for worker_id, worker in workers.items():
        prices = worker.get("prices", {})
        worker_rows.append(
            {
                "processo": worker_id,
                "status": worker.get("status"),
                "cluster": worker.get("cluster"),
                "moedas": ", ".join(worker.get("symbols", [])),
                "precos": ", ".join(f"{symbol}: {_fmt_price(price)}" for symbol, price in prices.items()),
            }
        )
    st.dataframe(worker_rows, use_container_width=True, hide_index=True)
else:
    st.info("Nenhum worker registrado ainda.")

st.subheader("Moedas Monitoradas")
symbols = status.get("symbols", [])
if symbols:
    st.dataframe(
        [
            {
                "moeda": item["symbol"],
                "cluster": item["cluster"],
                "preco": _fmt_price(item.get("price")),
                "candles": item.get("candles", {}),
                "atualizado": _fmt_timestamp(item.get("price_updated_at")),
            }
            for item in symbols
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Aguardando os primeiros dados do WebSocket.")

st.subheader("Ultimos Alertas")
alerts = status.get("alerts", [])
if alerts:
    for alert in alerts[:20]:
        st.markdown(f"**{alert['title']}**")
        st.write(alert["message"])
        st.json(alert["metrics"])
else:
    st.info("Nenhum alerta disparado ainda.")
