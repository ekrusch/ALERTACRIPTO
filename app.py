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


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "US$ 0.00"
    return f"US$ {value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "aguardando"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def _fmt_timestamp(value: float | None) -> str:
    if not value:
        return "aguardando"
    return datetime.fromtimestamp(value).strftime("%H:%M:%S")


st.set_page_config(page_title="Radar de Anomalias Cripto", layout="wide")
st.markdown("<meta http-equiv='refresh' content='10'>", unsafe_allow_html=True)
st.title("Radar de Anomalias Cripto")
st.caption("Atualizacao automatica a cada 10 segundos. Modo simulado: sem ordens reais.")

if not STATUS_FILE.exists():
    st.warning("O radar ainda nao gerou status. Rode: python -m radar.main")
    st.stop()

with STATUS_FILE.open("r", encoding="utf-8") as file:
    status = json.load(file)

updated_at = status.get("updated_at")
if updated_at:
    st.caption(f"Ultima atualizacao: {datetime.fromtimestamp(updated_at).strftime('%Y-%m-%d %H:%M:%S')}")

paper = status.get("paper", {})
st.subheader("Carteira Simulada")
metric_cols = st.columns(5)
metric_cols[0].metric("Banca inicial", _fmt_usd(paper.get("initial_cash")))
metric_cols[1].metric("Saldo livre", _fmt_usd(paper.get("cash")))
metric_cols[2].metric("Em posicoes", _fmt_usd(paper.get("open_value")))
metric_cols[3].metric("Patrimonio", _fmt_usd(paper.get("equity")), _fmt_pct(paper.get("total_pnl_pct")))
metric_cols[4].metric("Resultado", _fmt_usd(paper.get("total_pnl_usd")))

positions = paper.get("positions", [])
if positions:
    st.dataframe(
        [
            {
                "moeda": item.get("symbol"),
                "preco medio": _fmt_price(item.get("avg_price")),
                "preco atual": _fmt_price(item.get("last_price")),
                "valor atual": _fmt_usd(item.get("value_usd")),
                "resultado": _fmt_usd(item.get("pnl_usd")),
                "resultado %": _fmt_pct(item.get("pnl_pct")),
                "aberta": _fmt_timestamp(item.get("opened_at")),
            }
            for item in positions
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Nenhuma posicao simulada aberta. O paper trading compra apenas em alerta CONFIRMADO.")

st.subheader("Moedas Monitoradas")
symbols = status.get("symbols", [])
if symbols:
    st.dataframe(
        [
            {
                "moeda": item["symbol"],
                "cluster": item["cluster"],
                "preco": _fmt_price(item.get("price")),
                "preco inicial": _fmt_price(item.get("initial_price")),
                "variacao": _fmt_pct(item.get("change_pct")),
                "variacao ciclo": _fmt_pct(item.get("tick_change_pct")),
                "candles": item.get("candles", {}),
                "atualizado": _fmt_timestamp(item.get("price_updated_at")),
            }
            for item in sorted(symbols, key=lambda row: row.get("change_pct") or 0, reverse=True)
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Aguardando os primeiros dados do WebSocket.")

left_col, right_col = st.columns(2)

with left_col:
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

    st.subheader("Operacoes Simuladas")
    trades = paper.get("trades", [])
    if trades:
        st.dataframe(
            [
                {
                    "hora": _fmt_timestamp(item.get("ts")),
                    "lado": item.get("side"),
                    "moeda": item.get("symbol"),
                    "preco": _fmt_price(item.get("price")),
                    "valor": _fmt_usd(item.get("notional_usd")),
                    "resultado": _fmt_usd(item.get("pnl_usd")),
                    "motivo": item.get("reason"),
                }
                for item in trades[:20]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Nenhuma operacao simulada ainda.")

with right_col:
    st.subheader("Ultimos Alertas")
    alerts = status.get("alerts", [])
    if alerts:
        for alert in alerts[:20]:
            st.markdown(f"**{alert['title']}**")
            st.write(alert["message"])
            st.json(alert["metrics"])
    else:
        st.info("Nenhum alerta disparado ainda.")
