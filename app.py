from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


STATUS_FILE = Path("storage/status.json")
DISPLAY_TZ = ZoneInfo("America/Sao_Paulo")


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


def _fmt_compact_usd(value: float | None) -> str:
    if value is None:
        return "aguardando"
    if value >= 1_000_000_000:
        return f"US$ {value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"US$ {value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"US$ {value / 1_000:.1f}K"
    return f"US$ {value:.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "aguardando"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.2f}%"


def _fmt_timestamp(value: float | None) -> str:
    if not value:
        return "aguardando"
    return datetime.fromtimestamp(value, DISPLAY_TZ).strftime("%H:%M:%S")


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "aguardando"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}min"
    return f"{minutes / 60:.1f}h"


def _variation_sort_value(item: dict) -> float:
    change = item.get("change_24h_pct")
    if isinstance(change, (int, float)):
        return float(change)
    change = item.get("change_pct")
    if isinstance(change, (int, float)):
        return float(change)
    tick_change = item.get("tick_change_pct")
    if isinstance(tick_change, (int, float)):
        return float(tick_change)
    return -999999.0


def _exchange_from_cluster(cluster: str | None) -> str:
    text = (cluster or "").lower()
    if text.startswith("bybit") or "bybit" in text:
        return "Bybit"
    if text.startswith("mexc") or "mexc" in text:
        return "MEXC"
    if text.startswith("kucoin") or "kucoin" in text:
        return "KuCoin"
    return "Outras"


def _pct_html(value: float | None) -> str:
    if value is None:
        return '<span class="pct-neutral">aguardando</span>'
    if value > 0:
        return f'<span class="pct-up">{html.escape(_fmt_pct(value))}</span>'
    if value < 0:
        return f'<span class="pct-down">{html.escape(_fmt_pct(value))}</span>'
    return f'<span class="pct-neutral">{html.escape(_fmt_pct(value))}</span>'


def _pct_class(value: float | None) -> str:
    if value is None:
        return "pct-neutral"
    if value > 0:
        return "pct-up"
    if value < 0:
        return "pct-down"
    return "pct-neutral"


def _pct_cell_style(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text == "aguardando":
        return "color: #38bdf8; font-weight: 800;"
    if not text.endswith("%"):
        return ""
    try:
        number = float(text.replace("%", "").replace("+", ""))
    except ValueError:
        return ""
    if number > 0:
        return "color: #22c55e; font-weight: 800;"
    if number < 0:
        return "color: #ef4444; font-weight: 800;"
    return "color: #38bdf8; font-weight: 800;"


def _styled_dataframe(rows: list[dict]):
    dataframe = pd.DataFrame(rows)
    pct_columns = [
        column
        for column in dataframe.columns
        if "%" in str(column).lower()
        or "var " in str(column).lower()
        or str(column).lower().startswith("ganho")
        or str(column).lower().startswith("perda")
    ]
    if not pct_columns:
        return dataframe

    styler = dataframe.style
    if hasattr(styler, "map"):
        return styler.map(_pct_cell_style, subset=pct_columns)
    return styler.applymap(_pct_cell_style, subset=pct_columns)


def _render_symbols_table(rows: list[dict]) -> None:
    table_rows = []
    for item in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(item.get('cluster', '')))}</td>"
            f"<td>{html.escape(_fmt_price(item.get('price')))}</td>"
            f"<td>{html.escape(str(item.get('opportunity_score', 'aguardando')))}</td>"
            f"<td>{_pct_html(item.get('change_24h_pct'))}</td>"
            f"<td>{_pct_html(item.get('range_24h_pct'))}</td>"
            f"<td>{html.escape(_fmt_compact_usd(item.get('turnover_24h')))}</td>"
            f"<td>{html.escape(_fmt_price(item.get('initial_price')))}</td>"
            f"<td>{_pct_html(item.get('change_pct'))}</td>"
            f"<td>{_pct_html(item.get('tick_change_pct'))}</td>"
            f"<td>{html.escape(str(item.get('candles', {})))}</td>"
            f"<td>{html.escape(_fmt_timestamp(item.get('price_updated_at')))}</td>"
            "</tr>"
        )

    table_html = (
        """
        <!doctype html>
        <html>
        <head>
            <style>
                body {
                    margin: 0;
                    background: transparent;
                    color: #fafafa;
                    font-family: "Source Sans Pro", sans-serif;
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: auto;
                    font-size: 14px;
                }
                th, td {
                    border-bottom: 1px solid rgba(250, 250, 250, 0.13);
                    padding: 8px 10px;
                    text-align: left;
                    white-space: nowrap;
                }
                th {
                    background: rgba(250, 250, 250, 0.06);
                    color: rgba(250, 250, 250, 0.76);
                    font-weight: 600;
                }
                .pct-up {
                    color: #22c55e;
                    font-weight: 800;
                }
                .pct-down {
                    color: #ef4444;
                    font-weight: 800;
                }
                .pct-neutral {
                    color: #38bdf8;
                    font-weight: 800;
                }
            </style>
        </head>
        <body>
            <table>
                <thead>
                    <tr>
                        <th>moeda</th>
                        <th>cluster</th>
                        <th>preco</th>
                        <th>score</th>
                        <th>var 24h</th>
                        <th>range 24h</th>
                        <th>volume 24h</th>
                        <th>preco inicial</th>
                        <th>variacao</th>
                        <th>variacao ciclo</th>
                        <th>candles</th>
                        <th>atualizado</th>
                    </tr>
                </thead>
                <tbody>
        """
        + "".join(table_rows)
        + """
                </tbody>
            </table>
        </body>
        </html>
        """
    )
    components.html(
        table_html,
        height=44 * (len(rows) + 1) + 8,
        scrolling=False,
    )


def _position_rows(positions: list[dict], symbol_lookup: dict[str, dict]) -> list[dict]:
    rows = []
    for item in positions:
        symbol = item.get("symbol")
        symbol_data = symbol_lookup.get(symbol, {})
        active_signal = symbol_data.get("active_signal") or item
        rows.append(
            {
                "moeda": symbol,
                "cluster": symbol_data.get("cluster", ""),
                "preco medio": _fmt_price(item.get("avg_price")),
                "preco atual": _fmt_price(item.get("last_price")),
                "resultado": _fmt_usd(item.get("pnl_usd")),
                "resultado %": _fmt_pct(item.get("pnl_pct")),
                "var 24h": _fmt_pct(symbol_data.get("change_24h_pct")),
                "score": symbol_data.get("opportunity_score", "aguardando"),
                "stop movel": _fmt_price(active_signal.get("trailing_stop_price")),
                "topo monitorado": _fmt_price(active_signal.get("peak_price")),
                "aberta": _fmt_timestamp(item.get("opened_at")),
            }
        )
    return sorted(rows, key=lambda row: row["moeda"] or "")


def _closed_trade_rows(trades: list[dict]) -> list[dict]:
    rows = []
    for item in trades:
        if item.get("side") != "SELL":
            continue
        rows.append(
            {
                "fechada": _fmt_timestamp(item.get("closed_at") or item.get("ts")),
                "moeda": item.get("symbol"),
                "entrada": _fmt_price(item.get("entry_price")),
                "saida": _fmt_price(item.get("exit_price") or item.get("price")),
                "valor": _fmt_usd(item.get("notional_usd")),
                "resultado": _fmt_usd(item.get("pnl_usd")),
                "resultado %": _fmt_pct(item.get("pnl_pct")),
                "ganho max": _fmt_pct(item.get("max_gain_pct")),
                "duração": _fmt_duration(item.get("duration_seconds")),
                "aberta": _fmt_timestamp(item.get("opened_at")),
                "topo": _fmt_price(item.get("peak_price")),
                "stop": _fmt_price(item.get("trailing_stop_price")),
                "motivo": item.get("reason"),
                "entrada motivo": item.get("entry_reason"),
            }
        )
    return rows


def _loss_summary(closed_trades: list[dict]) -> dict[str, str] | None:
    losses = [item for item in closed_trades if isinstance(item.get("pnl_usd"), (int, float)) and item.get("pnl_usd") < 0]
    if not losses:
        return None
    avg_loss = sum(float(item.get("pnl_pct") or 0.0) for item in losses) / len(losses)
    worst = min(losses, key=lambda item: float(item.get("pnl_pct") or 0.0))
    reasons: dict[str, int] = {}
    for item in losses:
        reason = str(item.get("reason") or "sem motivo")
        reasons[reason] = reasons.get(reason, 0) + 1
    common_reason = max(reasons.items(), key=lambda item: item[1])[0]
    return {
        "perdas": str(len(losses)),
        "perda média": _fmt_pct(avg_loss),
        "pior perda": f"{worst.get('symbol')} {_fmt_pct(worst.get('pnl_pct'))}",
        "motivo comum": common_reason,
    }


def _paper_window_return(history: list[dict], current_equity: float | None, hours: int, now: float | None) -> tuple[float | None, float | None]:
    if not history or current_equity is None or current_equity <= 0 or not now:
        return None, None
    target = now - (hours * 60 * 60)
    baseline = None
    for item in history:
        ts = item.get("ts")
        equity = item.get("equity")
        if not isinstance(ts, (int, float)) or not isinstance(equity, (int, float)) or equity <= 0:
            continue
        if ts <= target:
            baseline = float(equity)
        else:
            break
    if baseline is None:
        return None, None
    gain_pct = 100.0 * (current_equity - baseline) / baseline
    return gain_pct, gain_pct / hours


def _render_performance_box(paper: dict, history: list[dict], now: float | None) -> None:
    current_equity = paper.get("equity")
    if not isinstance(current_equity, (int, float)):
        current_equity = None

    rows = []
    for hours, label in ((1, "1h"), (4, "4h"), (24, "24h")):
        gain_pct, hourly_pct = _paper_window_return(history, current_equity, hours, now)
        rows.append(
            "<tr>"
            f"<td>{label}</td>"
            f"<td class=\"{_pct_class(gain_pct)}\">{html.escape(_fmt_pct(gain_pct))}</td>"
            f"<td class=\"{_pct_class(hourly_pct)}\">{html.escape(_fmt_pct(hourly_pct))}/h</td>"
            "</tr>"
        )

    components.html(
        """
        <!doctype html>
        <html>
        <head>
            <style>
                body { margin: 0; background: transparent; font-family: "Source Sans Pro", sans-serif; color: #fafafa; }
                .box { border: 1px solid rgba(250,250,250,0.16); border-radius: 10px; padding: 10px 12px; background: rgba(250,250,250,0.04); }
                .title { font-size: 13px; font-weight: 800; margin-bottom: 8px; color: rgba(250,250,250,0.9); }
                table { width: 100%; border-collapse: collapse; font-size: 12px; }
                th, td { text-align: right; padding: 4px 2px; white-space: nowrap; }
                th:first-child, td:first-child { text-align: left; }
                th { color: rgba(250,250,250,0.62); font-weight: 700; }
                .pct-up { color: #22c55e; font-weight: 800; }
                .pct-down { color: #ef4444; font-weight: 800; }
                .pct-neutral { color: #38bdf8; font-weight: 800; }
            </style>
        </head>
        <body>
            <div class="box">
                <div class="title">Feedback Paper</div>
                <table>
                    <thead><tr><th>janela</th><th>ganho</th><th>%/h</th></tr></thead>
                    <tbody>
        """
        + "".join(rows)
        + """
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """,
        height=138,
        scrolling=False,
    )


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
    st.caption(f"Ultima atualizacao: {datetime.fromtimestamp(updated_at, DISPLAY_TZ).strftime('%Y-%m-%d %H:%M:%S')}")

paper = status.get("paper", {})
paper_history = status.get("paper_history", [])
symbols = status.get("symbols", [])
symbol_lookup = {item.get("symbol"): item for item in symbols if item.get("symbol")}
top_left, top_right = st.columns([4, 1])
with top_left:
    st.subheader("Carteira Simulada")
    metric_cols = st.columns(5)
    metric_cols[0].metric("Banca inicial", _fmt_usd(paper.get("initial_cash")))
    metric_cols[1].metric("Saldo livre", _fmt_usd(paper.get("cash")))
    metric_cols[2].metric("Em posicoes", _fmt_usd(paper.get("open_value")))
    metric_cols[3].metric("Patrimonio", _fmt_usd(paper.get("equity")), _fmt_pct(paper.get("total_pnl_pct")))
    metric_cols[4].metric("Resultado", _fmt_usd(paper.get("total_pnl_usd")))
with top_right:
    _render_performance_box(paper, paper_history if isinstance(paper_history, list) else [], updated_at)

positions = paper.get("positions", [])
st.subheader("Moedas em Negociação Agora")
if positions:
    st.dataframe(
        _styled_dataframe(_position_rows(positions, symbol_lookup)),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("Nenhuma moeda em negociação agora. O paper trading só aparece aqui quando abre posição de fato.")

st.subheader("Negociações Encerradas")
trades = paper.get("trades", [])
closed_trades = _closed_trade_rows(trades)
if closed_trades:
    loss_summary = _loss_summary([item for item in trades if item.get("side") == "SELL"])
    if loss_summary:
        loss_cols = st.columns(4)
        for column, (label, value) in zip(loss_cols, loss_summary.items()):
            column.metric(label, value)
    st.dataframe(
        _styled_dataframe(closed_trades),
        width="stretch",
        hide_index=True,
        height=min(900, 38 * (len(closed_trades) + 1)),
    )
else:
    st.info("Nenhuma negociação encerrada ainda.")

st.subheader("Top Oportunidades Agora")
opportunities = status.get("opportunities", [])
if opportunities:
    _render_symbols_table(opportunities[:30])
else:
    st.info("Aguardando dados 24h suficientes para montar o ranking.")

st.subheader("Moedas Monitoradas")
if symbols:
    grouped_symbols: dict[str, list[dict]] = {}
    for item in symbols:
        grouped_symbols.setdefault(_exchange_from_cluster(item.get("cluster")), []).append(item)

    for exchange in ("Bybit", "MEXC", "KuCoin", "Outras"):
        rows = sorted(grouped_symbols.get(exchange, []), key=_variation_sort_value, reverse=True)
        if not rows:
            continue
        st.markdown(f"#### {exchange}")
        _render_symbols_table(rows)
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
        st.dataframe(worker_rows, width="stretch", hide_index=True)
    else:
        st.info("Nenhum worker registrado ainda.")

    st.subheader("Operacoes Simuladas")
    if trades:
        st.dataframe(
            _styled_dataframe([
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
            ]),
            width="stretch",
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
