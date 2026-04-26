"""
Indicadores de regime de mercado (fontes públicas, sem API key obrigatória).
Usado pelo painel Streamlit — não acoplar ao processo do radar.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


USER_AGENT = "radar-cripto-regime/1.0 (Streamlit; +https://github.com/)"
TZ_BR = ZoneInfo("America/Sao_Paulo")


def _http_get(
    url: str,
    *,
    timeout: float = 20.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.getcode() or 200), resp.read()


def _json_loads(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


@dataclass
class RegimeField:
    label: str
    value: str
    detail: str = ""
    source: str = ""
    error: str | None = None


@dataclass
class RegimeSnapshot:
    fetched_at: float
    fields: list[RegimeField] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _fmt_usd_compact(x: float) -> str:
    ax = abs(x)
    if ax >= 1_000_000_000:
        return f"US$ {x / 1_000_000_000:,.2f}B"
    if ax >= 1_000_000:
        return f"US$ {x / 1_000_000:,.2f}M"
    if ax >= 1_000:
        return f"US$ {x / 1_000:,.2f}K"
    return f"US$ {x:,.2f}"


def _bybit_btc() -> dict[str, Any]:
    q = urllib.parse.urlencode({"category": "linear", "symbol": "BTCUSDT"})
    code, body = _http_get(f"https://api.bybit.com/v5/market/tickers?{q}")
    if code != 200:
        return {}
    data = _json_loads(body)
    if data.get("retCode") != 0:
        return {}
    arr = (data.get("result") or {}).get("list") or []
    if not arr:
        return {}
    return arr[0] if isinstance(arr[0], dict) else {}


def _bybit_account_ratio_1h() -> dict[str, Any] | None:
    q = urllib.parse.urlencode({"category": "linear", "symbol": "BTCUSDT", "period": "1h", "limit": "1"})
    try:
        code, body = _http_get(f"https://api.bybit.com/v5/market/account-ratio?{q}")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    if code != 200:
        return None
    data = _json_loads(body)
    if data.get("retCode") != 0:
        return None
    lst = (data.get("result") or {}).get("list") or []
    if not lst:
        return None
    r0 = lst[0]
    return r0 if isinstance(r0, dict) else None


def _coingecko_global() -> dict[str, Any] | None:
    try:
        code, body = _http_get("https://api.coingecko.com/api/v3/global")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    if code != 200:
        return None
    data = _json_loads(body)
    return data.get("data") if isinstance(data.get("data"), dict) else None


def _parse_coinshares_fund_flow_usd(html: str) -> tuple[float | None, str | None]:
    """
    Tenta extrair o primeiro fluxo semanal em US$ do texto da página CoinShares.
    Ex.: 'saw US$1.4B of inflows' ou 'US$1,116M'
    """
    # Normaliza
    t = html.replace("\u00a0", " ")
    # Padrões comuns no blog CoinShares
    m = re.search(
        r"(?:saw|recorded|total(?:led)?|products saw)\s+US\$\s*([0-9.,]+)\s*([bBmM])\s+of inflows",
        t,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"inflows? of\s+US\$\s*([0-9.,]+)\s*([bBmM])", t, re.IGNORECASE)
    if not m:
        m = re.search(r"US\$\s*([0-9.,]+)\s*([bBmB])\b[^U]{0,40}inflow", t, re.IGNORECASE)
    if not m:
        return None, None
    num_s = m.group(1).replace(",", "")
    try:
        v = float(num_s)
    except ValueError:
        return None, None
    u = m.group(2).upper()
    if u == "B":
        v *= 1_000_000_000.0
    else:
        v *= 1_000_000.0
    snippet = m.group(0)[:120]
    return v, snippet


def _fetch_coinshares_etf_flow() -> RegimeField:
    """
    Último relatório publicado em coinshares.com/insights/.../fund-flows-*
    Lista fixa de URLs recentes (atualize quando o blog mudar o slug).
    """
    candidates = [
        "https://coinshares.com/us/insights/research-data/fund-flows-20-04-26/",
        "https://coinshares.com/us/insights/research-data/fund-flows-13-04-26/",
        "https://coinshares.com/us/insights/research-data/fund-flows-07-04-26/",
    ]
    for url in candidates:
        try:
            code, body = _http_get(url)
        except (urllib.error.URLError, OSError, TimeoutError, UnicodeDecodeError):
            continue
        if code != 200:
            continue
        text = body.decode("utf-8", "replace")
        v, _snip = _parse_coinshares_fund_flow_usd(text)
        if v is not None:
            return RegimeField(
                label="ETF cripto (produtos ETP) — fluxo semanal (CoinShares)",
                value=_fmt_usd_compact(v),
                detail="Agregado global de fundos; EUA costuma ser a maior fatia. Atualize as URLs em regime_data.py se o post mais recente mudar.",
                source=url,
            )
    return RegimeField(
        label="ETF cripto (produtos ETP) — fluxo semanal (CoinShares)",
        value="—",
        detail="Não foi possível ler a página. Veja o relatório manual.",
        source="https://coinshares.com/us/insights/research/research-and-data",
        error="parse ou rede",
    )


def _manual_etf_file(app_root: Path) -> RegimeField | None:
    path = app_root / "config" / "regime_etf_override.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    usd = raw.get("weekly_flow_usd")
    as_of = raw.get("as_of")
    source = str(raw.get("source_note") or "config/regime_etf_override.json")
    if not isinstance(usd, (int, float)):
        return None
    return RegimeField(
        label="ETF (override manual em config/)",
        value=_fmt_usd_compact(float(usd)),
        detail=f"as_of: {as_of}" if as_of else "Valores inseridos localmente; sobrescreve a leitura automática se você implementar isso no app (opcional).",
        source=source,
    )


def _cme_oi_field() -> RegimeField:
    """
    Open interest CME: não há JSON público estável sem cadastro. Indicamos a página oficial.
    O site da CME mostra 'Open interest' no produto; aqui deixamos o número deixado como 'ver fonte'.
    """
    return RegimeField(
        label="Open interest (CME Bitcoin Futures)",
        value="ver página oficial (atraso e contrato ativo variam)",
        detail="A CME publica OI e volume por contrato. Para série contínua use o contrato 'front' atual.",
        source="https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin.html",
    )


def _perp_oi_bybit() -> RegimeField:
    row = _bybit_btc()
    if not row:
        return RegimeField(
            label="Open interest (perp BTCUSDT, Bybit)",
            value="—",
            error="Bybit indisponível",
            source="https://bybit.com",
        )
    try:
        oi_usd = float(row.get("openInterestValue") or 0.0)
    except (TypeError, ValueError):
        oi_usd = 0.0
    oi_c = str(row.get("openInterest") or "")
    return RegimeField(
        label="Open interest (perp BTCUSDT, Bybit, USD)",
        value=_fmt_usd_compact(oi_usd) if oi_usd else "—",
        detail=f"Contratos: {oi_c} BTC aprox. no ticker (linear).",
        source="https://bybit.com",
    )


def _funding_basis() -> tuple[RegimeField, RegimeField]:
    row = _bybit_btc()
    if not row:
        f = RegimeField(
            label="Funding (perp BTC, Bybit, 8h, anualizado aprox.)",
            value="—",
            error="Bybit",
            source="https://bybit.com",
        )
        b = RegimeField(
            label="Base perp: (mark − index) / index",
            value="—",
            error="Bybit",
            source="https://bybit.com",
        )
        return f, b
    try:
        fr = float(row.get("fundingRate") or 0.0)
        mark = float(row.get("markPrice") or 0.0)
        index = float(row.get("indexPrice") or 0.0)
    except (TypeError, ValueError):
        fr, mark, index = 0.0, 0.0, 0.0
    apr = fr * 3.0 * 365.0 * 100.0
    base_pct = 100.0 * (mark - index) / index if index > 0 else 0.0
    f = RegimeField(
        label="Funding (perp BTC, Bybit) — aprox. APR",
        value=f"{apr:+.2f}% /ano (8h: {fr * 100:.4f}%)",
        detail="APR ≈ taxa × 3 × 365; funding real depende de intervalo e teto de funding.",
        source="https://bybit.com",
    )
    b = RegimeField(
        label="Base perp: (mark − index) / index (Bybit)",
        value=f"{base_pct:+.3f}%",
        detail="Positivo: perp acima do índice; negativo: desconto.",
        source="https://bybit.com",
    )
    return f, b


def _flow_proxy() -> RegimeField:
    """
    CVD agregado spot exige fornecedor pago. Proxy: razão de contas (buy) na Bybit, última hora.
    """
    r = _bybit_account_ratio_1h()
    if not r:
        return RegimeField(
            label="Fluxo (proxy) — participação de compra 1h (Bybit, BTC linear)",
            value="—",
            detail="Substituto aproximado de CVD agregado; leia como pressão de compra vs venda na Bybit, não CVD on-chain.",
            error="rede",
            source="https://bybit.com",
        )
    try:
        br = float(r.get("buyRatio") or 0.0)
    except (TypeError, ValueError):
        br = 0.0
    try:
        ts = int(r.get("timestamp", 0) or 0) / 1000.0
    except (TypeError, ValueError):
        ts = 0.0
    tstr = (
        datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ_BR).strftime("%Y-%m-%d %H:%M %Z")
        if ts
        else "—"
    )
    return RegimeField(
        label="Fluxo (proxy) — buy ratio 1h (Bybit, BTC perp)",
        value=f"{100.0 * br:.1f}% compras (vs {100.0 * (1 - br):.1f}% vendas)",
        detail=f"Última barra: {tstr}. Não é CVD spot; é métrica de contas/posições publicada pela corretora.",
        source="https://bybit.com",
    )


def _dominance() -> RegimeField:
    g = _coingecko_global()
    if not g:
        return RegimeField(
            label="Dominância BTC (CoinGecko, market cap %)",
            value="—",
            error="CoinGecko",
            source="https://www.coingecko.com",
        )
    mcp = g.get("market_cap_percentage")
    mcp = mcp if isinstance(mcp, dict) else {}
    try:
        btc = float(mcp.get("btc") or 0.0)
    except (TypeError, ValueError):
        btc = 0.0
    updated = g.get("updated_at")
    tnote = ""
    if isinstance(updated, (int, float)):
        tnote = " atualizado: " + datetime.fromtimestamp(
            int(updated), tz=timezone.utc
        ).astimezone(TZ_BR).strftime("%Y-%m-%d %H:%M %Z")
    return RegimeField(
        label="Dominância BTC (CoinGecko)",
        value=f"{btc:.2f}% do market cap de cripto (aprox.)",
        detail=tnote.strip() or "Participação aproximada de BTC no universo que o CoinGecko rastreia.",
        source="https://api.coingecko.com/api/v3/global",
    )


def fetch_regime_snapshot(app_root: Path | None = None) -> RegimeSnapshot:
    app_root = app_root or Path(__file__).resolve().parent
    t0 = time.time()
    fields: list[RegimeField] = []
    notes: list[str] = []

    manual = _manual_etf_file(app_root)
    if manual is not None:
        fields.append(manual)
        notes.append("ETF: usando `config/regime_etf_override.json`. Apague o arquivo para voltar à leitura automática (CoinShares).")
    else:
        fields.append(_fetch_coinshares_etf_flow())
    fields.append(_cme_oi_field())
    fields.append(_perp_oi_bybit())
    f, b = _funding_basis()
    fields.append(f)
    fields.append(b)
    fields.append(_flow_proxy())
    fields.append(_dominance())
    return RegimeSnapshot(fetched_at=t0, fields=fields, notes=notes)
