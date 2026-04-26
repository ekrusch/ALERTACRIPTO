"""
Contexto de timeframe maior (D1 / H4) via APIs públicas das corretoras — não passa pelo processo WebSocket do radar.
Usado pelo Streamlit para checklist visual (viés + zona), alinhado ao fluxo: D1 → H4 → gatilho (radar em H1/15m).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

ExchangeKind = Literal["bybit_linear", "mexc_spot", "kucoin_spot", "unknown"]


def _get(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "radar-cripto-htf/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def exchange_from_cluster(cluster: str | None) -> ExchangeKind:
    t = (cluster or "").lower()
    if "bybit" in t:
        return "bybit_linear"
    if "mexc" in t:
        return "mexc_spot"
    if "kucoin" in t:
        return "kucoin_spot"
    return "unknown"


def _kucoin_symbol(symbol: str) -> str:
    if "-" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}-USDT"
    return symbol


def _bybit_klines(symbol: str, interval: str, limit: int = 40) -> list[list[str]]:
    q = urllib.parse.urlencode(
        {"category": "linear", "symbol": symbol, "interval": interval, "limit": str(limit)}
    )
    data = _get(f"https://api.bybit.com/v5/market/kline?{q}")
    if data.get("retCode") != 0:
        return []
    lst = (data.get("result") or {}).get("list") or []
    good = [x for x in lst if isinstance(x, list) and len(x) >= 7]
    return list(reversed(good))  # API entrega a mais recente primeiro


def _mexc_klines(symbol: str, interval: str, limit: int = 40) -> list[list[str]]:
    q = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": str(limit)})
    data = _get(f"https://api.mexc.com/api/v3/klines?{q}")
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, list) and len(x) >= 6]


def _kucoin_klines(symbol: str, interval: Literal["1day", "4hour"], limit: int = 40) -> list[list[str]]:
    """KuCoin: type 1day / 4hour com startAt/endAt (igual `radar/connectors/kucoin.py`)."""
    import time as _t

    sym = _kucoin_symbol(symbol)
    step = 86400 if interval == "1day" else 4 * 3600
    end_at = int(_t.time())
    start_at = end_at - step * (limit + 2)
    q = urllib.parse.urlencode({"type": interval, "symbol": sym, "startAt": str(start_at), "endAt": str(end_at)})
    data = _get(f"https://api.kucoin.com/api/v1/market/candles?{q}")
    if not isinstance(data, dict) or data.get("code") != "200000":
        return []
    payload = data.get("data")
    if not isinstance(payload, list):
        return []
    out: list[list[str]] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            continue
        ts, o, c, h, low, vol = row[0], row[1], row[2], row[3], row[4], row[5]
        out.append([str(int(float(ts)) * 1000), str(o), str(h), str(low), str(c), str(vol), "0"])
    return sorted(out, key=lambda r: int(float(r[0])))


def _closes(rows: list[list[str]]) -> list[float]:
    out: list[float] = []
    for row in rows:
        try:
            out.append(float(row[4]))
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _sma(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    return sum(values[-length:]) / float(length)


def _d1_bias(closes_d: list[float]) -> tuple[str, str]:
    """Último fechamento diário vs SMA20 como proxy de viés."""
    if len(closes_d) < 21:
        return "sem dados", "precisa de ≥21 velas diárias"
    last = closes_d[-1]
    sma = _sma(closes_d, 20)
    if sma is None:
        return "sem dados", ""
    diff_pct = 100.0 * (last - sma) / sma if sma else 0.0
    if last > sma * 1.002:
        return "favor long", f"fech. D1 > SMA20 (~{diff_pct:+.2f}% vs média)"
    if last < sma * 0.998:
        return "favor short", f"fech. D1 < SMA20 (~{diff_pct:+.2f}% vs média)"
    return "neutro / meio", f"próximo da SMA20 (~{diff_pct:+.2f}%)"


def _h4_zone(closes_h4: list[float], highs: list[float], lows: list[float]) -> tuple[str, str]:
    """Posição do preço no range recente (últimas ~30 velas H4)."""
    n = min(len(closes_h4), len(highs), len(lows), 30)
    if n < 8:
        return "sem dados", "poucas velas H4"
    h = highs[-n:]
    l = lows[-n:]
    hi = max(h)
    lo = min(l)
    last = closes_h4[-1]
    if hi <= lo:
        return "—", ""
    pos = (last - lo) / (hi - lo)
    if pos >= 0.66:
        return "zona alta (possível resistência)", f"~{100*pos:.0f}% do range {n}×H4"
    if pos <= 0.34:
        return "zona baixa (possível suporte)", f"~{100*pos:.0f}% do range {n}×H4"
    return "zona intermediária", f"~{100*pos:.0f}% do range {n}×H4"


def _hl_from_rows(rows: list[list[str]]) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in rows:
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
        except (IndexError, TypeError, ValueError):
            continue
    return highs, lows, closes


@dataclass
class HTFContext:
    symbol: str
    exchange: str
    d1_bias: str
    d1_detail: str
    h4_zone: str
    h4_detail: str
    error: str | None = None


def fetch_htf_context(symbol: str, cluster: str | None) -> HTFContext:
    ex = exchange_from_cluster(cluster)
    if ex == "unknown":
        return HTFContext(
            symbol=symbol,
            exchange="?",
            d1_bias="—",
            d1_detail="Defina cluster Bybit/MEXC/KuCoin no status",
            h4_zone="—",
            h4_detail="",
            error="exchange",
        )

    try:
        if ex == "bybit_linear":
            d_rows = _bybit_klines(symbol, "D", 30)
            h_rows = _bybit_klines(symbol, "240", 40)
        elif ex == "mexc_spot":
            d_rows = _mexc_klines(symbol, "1d", 30)
            h_rows = _mexc_klines(symbol, "4h", 40)
        else:
            d_rows = _kucoin_klines(symbol, "1day", 30)
            h_rows = _kucoin_klines(symbol, "4hour", 40)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return HTFContext(
            symbol=symbol,
            exchange=ex,
            d1_bias="erro",
            d1_detail=str(exc)[:80],
            h4_zone="erro",
            h4_detail="",
            error="rede",
        )

    d_closes = _closes(d_rows)
    hh, ll, h_closes = _hl_from_rows(h_rows)

    b1, d1d = _d1_bias(d_closes)
    hz, hd = _h4_zone(h_closes, hh, ll)

    return HTFContext(
        symbol=symbol,
        exchange=ex,
        d1_bias=b1,
        d1_detail=d1d,
        h4_zone=hz,
        h4_detail=hd,
    )


def tradingview_symbol_url(symbol: str, cluster: str | None) -> str:
    ex = exchange_from_cluster(cluster)
    if ex == "bybit_linear":
        return f"https://www.tradingview.com/chart/?symbol=BYBIT:{symbol}"
    if ex == "mexc_spot":
        return f"https://www.tradingview.com/chart/?symbol=MEXC:{symbol}"
    if ex == "kucoin_spot":
        sym = _kucoin_symbol(symbol).replace("-", "")
        return f"https://www.tradingview.com/chart/?symbol=KUCOIN:{sym}"
    return f"https://www.tradingview.com/chart/?symbol={symbol}"
