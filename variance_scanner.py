"""
Scanner de maiores variações 24h (universo amplo) via APIs públicas.
Independente das regras de anomalia do radar — só classifica por |Δ%| e volume.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "bybit_linear": {
        "enabled": True,
        "min_abs_change_pct": 10.0,
        "min_turnover_24h_usd": 2_000_000.0,
        "top_n": 50,
        "usdt_perpet_only": True,
    },
    "mexc_spot": {
        "enabled": True,
        "min_abs_change_pct": 10.0,
        "min_quote_volume_24h_usd": 500_000.0,
        "top_n": 40,
        "usdt_only": True,
    },
}


def _load_config(app_root: Path) -> dict[str, Any]:
    path = app_root / "config" / "variance_scanner.json"
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path.exists():
        return out
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if isinstance(user, dict):
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key].update(value)
            else:
                out[key] = value
    return out


def _get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "radar-cripto-variance/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class TickerRow:
    symbol: str
    change_24h_pct: float
    turnover_24h_usd: float
    last_price: float
    venue: str
    high_24h: float | None
    low_24h: float | None


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_bybit_linear_movers(
    min_abs_change_pct: float,
    min_turnover_24h_usd: float,
    top_n: int,
    usdt_perpet_only: bool,
) -> list[TickerRow]:
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    data = _get_json(url)
    if data.get("retCode") != 0:
        return []
    raw = (data.get("result") or {}).get("list") or []
    rows: list[TickerRow] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "")
        if usdt_perpet_only and not sym.endswith("USDT"):
            continue
        p24 = _safe_float(item.get("price24hPcnt"))
        if p24 is None:
            continue
        ch = p24 * 100.0
        to = _safe_float(item.get("turnover24h")) or 0.0
        if to < min_turnover_24h_usd or abs(ch) < min_abs_change_pct:
            continue
        last = _safe_float(item.get("lastPrice")) or 0.0
        rows.append(
            TickerRow(
                symbol=sym,
                change_24h_pct=ch,
                turnover_24h_usd=to,
                last_price=last,
                venue="bybit_linear",
                high_24h=_safe_float(item.get("highPrice24h")),
                low_24h=_safe_float(item.get("lowPrice24h")),
            )
        )
    rows.sort(key=lambda r: abs(r.change_24h_pct), reverse=True)
    return rows[:top_n]


def fetch_mexc_spot_movers(
    min_abs_change_pct: float,
    min_quote_volume_24h_usd: float,
    top_n: int,
    usdt_only: bool,
) -> list[TickerRow]:
    data = _get_json("https://api.mexc.com/api/v3/ticker/24hr")
    if not isinstance(data, list):
        return []
    rows: list[TickerRow] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "")
        if usdt_only and not sym.endswith("USDT"):
            continue
        pcp = _safe_float(item.get("priceChangePercent"))
        if pcp is None:
            continue
        ch = pcp * 100.0
        qv = _safe_float(item.get("quoteVolume")) or 0.0
        if qv < min_quote_volume_24h_usd or abs(ch) < min_abs_change_pct:
            continue
        last = _safe_float(item.get("lastPrice")) or 0.0
        rows.append(
            TickerRow(
                symbol=sym,
                change_24h_pct=ch,
                turnover_24h_usd=qv,
                last_price=last,
                venue="mexc_spot",
                high_24h=_safe_float(item.get("highPrice")),
                low_24h=_safe_float(item.get("lowPrice")),
            )
        )
    rows.sort(key=lambda r: abs(r.change_24h_pct), reverse=True)
    return rows[:top_n]


def tv_url(row: TickerRow) -> str:
    if row.venue == "bybit_linear":
        return f"https://www.tradingview.com/chart/?symbol=BYBIT:{row.symbol}"
    return f"https://www.tradingview.com/chart/?symbol=MEXC:{row.symbol}"


def run_scanner(app_root: Path | None = None) -> dict[str, Any]:
    root = app_root or Path(__file__).resolve().parent
    cfg = _load_config(root)
    out: dict[str, Any] = {"config": cfg, "bybit": [], "mexc": [], "errors": []}
    bb = cfg.get("bybit_linear") or {}
    if bool(bb.get("enabled", True)):
        try:
            out["bybit"] = fetch_bybit_linear_movers(
                min_abs_change_pct=float(bb.get("min_abs_change_pct", 7.0)),
                min_turnover_24h_usd=float(bb.get("min_turnover_24h_usd", 2_000_000.0)),
                top_n=int(bb.get("top_n", 50)),
                usdt_perpet_only=bool(bb.get("usdt_perpet_only", True)),
            )
        except (urllib.error.URLError, OSError, TimeoutError, TypeError, ValueError) as exc:
            out["errors"].append(f"Bybit: {exc}")
    mx = cfg.get("mexc_spot") or {}
    if bool(mx.get("enabled", True)):
        try:
            out["mexc"] = fetch_mexc_spot_movers(
                min_abs_change_pct=float(mx.get("min_abs_change_pct", 7.0)),
                min_quote_volume_24h_usd=float(mx.get("min_quote_volume_24h_usd", 500_000.0)),
                top_n=int(mx.get("top_n", 40)),
                usdt_only=bool(mx.get("usdt_only", True)),
            )
        except (urllib.error.URLError, OSError, TimeoutError, TypeError, ValueError) as exc:
            out["errors"].append(f"MEXC: {exc}")
    return out
