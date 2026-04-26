#!/usr/bin/env python3
"""
Le storage/trade_audit.jsonl (ou PAPER_AUDIT_FILE) e imprime agregados das
vendas (negociacoes encerradas) para revisao de estrategia.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]


def _audit_path() -> Path:
    env = os.getenv("PAPER_AUDIT_FILE")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    return ROOT / "storage" / "trade_audit.jsonl"


def _rule_from_reason(prefix: str, reason: str) -> str:
    r = (reason or "").strip()
    if r.lower().startswith(prefix):
        return r.split(":", 1)[-1].strip() or "unknown"
    return r or "unknown"


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"arquivo nao encontrado: {path}")
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _summarize_sells(sells: list[dict[str, Any]]) -> None:
    n = len(sells)
    if n == 0:
        print("nenhuma venda (SELL) no audit.")
        return

    pnls = [float(s.get("pnl_usd") or 0) for s in sells]
    wins = [p for p in pnls if p > 0]
    breakeven = [p for p in pnls if p == 0]
    losses = [p for p in pnls if p < 0]

    print("=== visao geral (SELL) ===")
    print(f"  fechadas:        {n}")
    print(f"  ganhos (pnl>0): {len(wins)}  |  zero: {len(breakeven)}  |  perdas: {len(losses)}")
    print(f"  pnl total USD:  {sum(pnls):.2f}")
    if pnls:
        print(f"  pnl medio USD:  {statistics.mean(pnls):.4f}  (mediana: {statistics.median(pnls):.4f})")
    print()

    by_exit: Counter[str] = Counter()
    by_entry: Counter[str] = Counter()
    pnl_by_exit: dict[str, list[float]] = defaultdict(list)
    pnl_by_entry: dict[str, list[float]] = defaultdict(list)
    sym_pnl: dict[str, float] = defaultdict(float)
    sym_n: Counter[str] = Counter()

    for s in sells:
        er = s.get("reason") or ""
        ex_rule = _rule_from_reason("saida", er)
        by_exit[ex_rule] += 1
        pnl_by_exit[ex_rule].append(float(s.get("pnl_usd") or 0))

        en_reason = s.get("entry_reason") or ""
        en_rule = _rule_from_reason("entrada", en_reason)
        by_entry[en_rule] += 1
        pnl_by_entry[en_rule].append(float(s.get("pnl_usd") or 0))

        sym = str(s.get("symbol") or "?")
        sym_pnl[sym] += float(s.get("pnl_usd") or 0)
        sym_n[sym] += 1

    def _print_ranked(
        title: str,
        keys: list[str],
        counter: Counter[str],
        pnl_map: dict[str, list[float]],
    ) -> None:
        print(f"=== {title} ===")
        for k in keys:
            c = counter[k]
            ps = pnl_map[k]
            tot = sum(ps)
            w = len([x for x in ps if x > 0])
            av = tot / c if c else 0.0
            print(f"  {k:42}  n={c:4}  win={w:4}  pnl_sum={tot:10.2f}  pnl_medio={av:8.4f}")
        print()

    exit_keys = sorted(by_exit.keys(), key=lambda k: (sum(pnl_by_exit[k]), by_exit[k]))
    _print_ranked("motivo de saida (exit rule)", exit_keys, by_exit, pnl_by_exit)
    entry_keys = sorted(by_entry.keys(), key=lambda k: (sum(pnl_by_entry[k]), by_entry[k]))
    _print_ranked("regra de entrada (entry_reason)", entry_keys, by_entry, pnl_by_entry)

    worst_syms = sorted(sym_pnl.items(), key=lambda x: x[1])[:20]
    print("=== piores 20 simbolos (soma pnl) ===")
    for sym, p in worst_syms:
        print(f"  {sym:20}  trades={sym_n[sym]:3}  pnl_sum={p:10.2f}")
    print()

    mfe_lost: list[tuple[str, float, float, str]] = []
    for s in sells:
        m = s.get("max_gain_pct")
        pnl_pct = s.get("pnl_pct")
        if m is None or pnl_pct is None:
            continue
        try:
            mf, pp = float(m), float(pnl_pct)
        except (TypeError, ValueError):
            continue
        if mf > 0.3 and pp < 0 and mf > abs(pp) + 0.1:
            mfe_lost.append(
                (str(s.get("symbol")), mf, pp, str(s.get("reason") or "")),
            )

    mfe_lost.sort(key=lambda x: -x[1])
    print(
        f"=== trades com lucro flutuante (max_gain_pct) mas fechou no vermelho "
        f"(amostra max 15 de {len(mfe_lost)}) ==="
    )
    for sym, mf, pp, r in mfe_lost[:15]:
        print(f"  {sym:18}  MFE%={mf:6.2f}  pnl%={pp:6.2f}  {r[:55]}")
    print()

    dur_s = []
    for s in sells:
        d = s.get("duration_seconds")
        if d is not None:
            try:
                dur_s.append(float(d))
            except (TypeError, ValueError):
                pass
    if dur_s:
        print("=== duracao (segundos) ate saida ===")
        print(
            f"  media={statistics.mean(dur_s):.0f}  mediana={statistics.median(dur_s):.0f}  "
            f"min={min(dur_s):.0f}  max={max(dur_s):.0f}"
        )
    print()

    q_scores: list[float] = []
    for s in sells:
        em = s.get("entry_metrics")
        if isinstance(em, dict) and "quality_score" in em:
            try:
                q_scores.append(float(em["quality_score"]))
            except (TypeError, ValueError):
                pass
    if q_scores:
        print("=== quality_score (na entrada) — apenas vendas ===")
        print(
            f"  media={statistics.mean(q_scores):.1f}  mediana={statistics.median(q_scores):.1f}  "
            f"min={min(q_scores):.1f}  max={max(q_scores):.1f}"
        )


def main() -> None:
    path = _audit_path()
    events = _load_events(path)
    buys = [e for e in events if (e.get("side") or "").upper() == "BUY"]
    sells = [e for e in events if (e.get("side") or "").upper() == "SELL"]
    print(f"arquivo: {path}")
    print(f"linhas carregadas: {len(events)}  (BUY={len(buys)}  SELL={len(sells)})")
    print()
    _summarize_sells(sells)


if __name__ == "__main__":
    main()
