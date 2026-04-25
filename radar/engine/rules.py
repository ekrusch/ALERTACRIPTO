from __future__ import annotations

from dataclasses import dataclass

from radar.config import ClusterConfig
from radar.engine.state import Candle, MarketState, SymbolState


@dataclass(frozen=True)
class Alert:
    symbol: str
    cluster_id: str
    cluster_name: str
    rule: str
    price: float
    title: str
    message: str
    metrics: dict[str, float | str]


def evaluate_symbol(state: SymbolState, cluster: ClusterConfig, market_state: MarketState | None = None) -> Alert | None:
    if state.price is None:
        return None

    exit_alert = _evaluate_exit_signal(state, cluster)
    if exit_alert is not None:
        return exit_alert

    alert = _evaluate_wave_surf_entry(state, cluster)
    if alert is not None:
        return _quality_gate_entry_alert(state, cluster, alert, market_state)

    alert: Alert | None = None
    if cluster.rule == "cvd_rvol_compression":
        alert = _evaluate_cvd_rvol_compression(state, cluster)
    elif cluster.rule == "orderbook_imbalance_vwap":
        alert = _evaluate_orderbook_imbalance_vwap(state, cluster)
    elif cluster.rule == "support_absorption_reversal":
        alert = _evaluate_support_absorption_reversal(state, cluster)
    elif cluster.rule == "microcap_spread_volume_anomaly":
        alert = _evaluate_microcap_spread_volume_anomaly(state, cluster)

    if alert is None:
        return None
    return _quality_gate_entry_alert(state, cluster, alert, market_state)


def _evaluate_exit_signal(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    if cluster.rule not in state.active_signals:
        return None

    trailing_stop_alert = _evaluate_trailing_stop_exit(state, cluster)
    if trailing_stop_alert is not None:
        return trailing_stop_alert

    exit_rule = f"{cluster.rule}_exit"
    if not state.can_alert(exit_rule, float(cluster.settings.get("exit_cooldown_minutes", 10))):
        return None

    if cluster.rule == "cvd_rvol_compression":
        return _evaluate_cvd_exit(state, cluster)
    if cluster.rule == "orderbook_imbalance_vwap":
        return _evaluate_orderbook_exit(state, cluster)
    if cluster.rule == "support_absorption_reversal":
        return _evaluate_support_exit(state, cluster)
    if cluster.rule == "microcap_spread_volume_anomaly":
        return _evaluate_microcap_exit(state, cluster)
    return None


def _exit_alert(
    state: SymbolState,
    cluster: ClusterConfig,
    reason: str,
    metrics: dict[str, float | str],
) -> Alert:
    exit_rule = f"{cluster.rule}_exit"
    state.mark_alert(exit_rule)
    state.deactivate_signal(cluster.rule)
    return Alert(
        symbol=state.symbol,
        cluster_id=cluster.id,
        cluster_name=cluster.name,
        rule=exit_rule,
        price=state.price or 0.0,
        title=f"Saida/risco em {state.symbol}",
        message=reason,
        metrics={"alert_level": "SAIDA", "price": round(state.price or 0.0, 8), **metrics},
    )


def _evaluate_trailing_stop_exit(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    active = state.active_signals[cluster.rule]
    stop_pct = _metric_float(active.get("trailing_stop_pct"))
    if stop_pct is None or stop_pct <= 0 or state.price is None or state.price <= 0:
        return None

    entry_price = float(active.get("price", state.price) or state.price)
    peak_price = max(float(active.get("peak_price", entry_price) or entry_price), state.price)
    active["peak_price"] = peak_price
    trailing_stop_price = peak_price * (1.0 - stop_pct / 100.0)
    active["trailing_stop_price"] = trailing_stop_price

    if state.price <= trailing_stop_price:
        drawdown_pct = 100.0 * (peak_price - state.price) / peak_price if peak_price > 0 else 0.0
        return _exit_alert(
            state,
            cluster,
            f"Stop movel de {stop_pct:.2f}% acionado. O preco devolveu a partir do topo monitorado.",
            {
                "entry_price": round(entry_price, 8),
                "peak_price": round(peak_price, 8),
                "trailing_stop_pct": round(stop_pct, 2),
                "trailing_stop_price": round(trailing_stop_price, 8),
                "drawdown_from_peak_pct": round(drawdown_pct, 2),
            },
        )
    return None


def _activate_entry(state: SymbolState, cluster: ClusterConfig, alert: Alert) -> Alert:
    level = str(alert.metrics.get("alert_level", "CONFIRMADO"))
    state.activate_signal(cluster.rule, alert.price, level, alert.metrics)
    return alert


def _evaluate_cvd_exit(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    candles_15m = _confirmed_candles(list(state.candles.get("15", [])))
    ma_length = int(settings.get("volume_ma_length", 20))
    if len(candles_15m) < ma_length + 1:
        return None

    latest = candles_15m[-1]
    volume_avg = _avg_volume(candles_15m[-ma_length - 1 : -1])
    if volume_avg <= 0:
        return None

    cvd_15m = state.cvd_since(15 * 60 * 1000)
    cvd_ratio = cvd_15m / volume_avg
    active_price = float(state.active_signals[cluster.rule].get("price", state.price or 0.0))
    price_drop_pct = 100.0 * (active_price - latest.close) / active_price if active_price > 0 else 0.0

    if cvd_ratio <= -float(settings.get("exit_negative_cvd_ratio", 0.2)) or price_drop_pct >= float(settings.get("exit_price_drop_pct", 2.0)):
        return _exit_alert(
            state,
            cluster,
            "A compra que sustentava o sinal perdeu forca. O saldo recente virou vendedor ou o preco devolveu demais.",
            {
                "cvd_15m": round(cvd_15m, 4),
                "cvd_ratio": round(cvd_ratio, 2),
                "price_drop_pct": round(price_drop_pct, 2),
            },
        )
    return None


def _evaluate_orderbook_exit(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    candles_1h = _confirmed_candles(list(state.candles.get("60", [])))
    if len(candles_1h) < 24:
        return None

    weekly_vwap = _vwap(candles_1h[-int(settings.get("vwap_lookback_hours", 168)) :])
    book = state.orderbook
    bid_ask_ratio = book.bid_notional_band / book.ask_notional_band if book.ask_notional_band > 0 else 0.0
    vwap_loss_pct = float(settings.get("exit_vwap_loss_pct", 0.35))
    lost_vwap = state.price is not None and weekly_vwap > 0 and state.price < weekly_vwap * (1.0 - vwap_loss_pct / 100.0)
    book_flipped = bid_ask_ratio > 0 and bid_ask_ratio <= float(settings.get("exit_bid_ask_ratio", 0.85))
    active = state.active_signals[cluster.rule]
    weak_reads = int(active.get("exit_weak_reads", 0))
    weak_reads = weak_reads + 1 if book_flipped else 0
    active["exit_weak_reads"] = weak_reads
    lost_vwap_reads = int(active.get("exit_lost_vwap_reads", 0))
    lost_vwap_reads = lost_vwap_reads + 1 if lost_vwap else 0
    active["exit_lost_vwap_reads"] = lost_vwap_reads

    if lost_vwap_reads >= int(settings.get("exit_lost_vwap_reads", 2)) or weak_reads >= int(settings.get("exit_weak_reads", 3)):
        reason = "O rompimento perdeu sustentacao: o preco perdeu a VWAP ou o book virou contra a compra."
        return _exit_alert(
            state,
            cluster,
            reason,
            {
                "bid_ask_ratio_2pct": round(bid_ask_ratio, 2),
                "weekly_vwap": round(weekly_vwap, 8),
                "lost_vwap": "sim" if lost_vwap else "nao",
                "lost_vwap_reads": lost_vwap_reads,
                "weak_book_reads": weak_reads,
            },
        )
    return None


def _evaluate_support_exit(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    active = state.active_signals[cluster.rule]
    support = float(active.get("support", 0.0) or 0.0)
    if support <= 0:
        higher_timeframe = str(settings.get("higher_timeframe", "240"))
        candles = _confirmed_candles(list(state.candles.get(higher_timeframe, [])))
        if len(candles) < int(settings.get("support_lookback_candles", 30)):
            return None
        support = min(candle.low for candle in candles[-int(settings.get("support_lookback_candles", 30)) :])

    break_pct = float(settings.get("exit_support_break_pct", 1.0))
    if state.price is not None and state.price < support * (1.0 - break_pct / 100.0):
        return _exit_alert(
            state,
            cluster,
            "A regiao que deveria segurar o preco foi perdida. A ideia de absorcao/suporte enfraqueceu.",
            {
                "support": round(support, 8),
                "distance_from_support_pct": round(100.0 * (state.price - support) / support, 2),
            },
        )
    return None


def _evaluate_microcap_exit(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    book = state.orderbook
    if state.price is None or not book.bids or not book.asks:
        return None

    best_bid = max(book.bids)
    best_ask = min(book.asks)
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None

    spread_pct = 100.0 * (best_ask - best_bid) / mid
    average_spread = state.average_spread(exclude_latest=True)
    cvd = state.cvd_since(int(settings.get("cvd_window_minutes", 15)) * 60 * 1000)
    active_price = float(state.active_signals[cluster.rule].get("price", state.price))
    price_drop_pct = 100.0 * (active_price - state.price) / active_price if active_price > 0 else 0.0
    spread_opened = average_spread > 0 and spread_pct >= average_spread * float(settings.get("exit_spread_expansion_ratio", 1.4))
    cvd_negative = cvd < 0

    if spread_opened or cvd_negative or price_drop_pct >= float(settings.get("exit_price_drop_pct", 3.0)):
        return _exit_alert(
            state,
            cluster,
            "A microcap perdeu qualidade: spread abriu, compra sumiu ou o preco devolveu rapido.",
            {
                "spread_pct": round(spread_pct, 3),
                "avg_spread_pct": round(average_spread, 3),
                "cvd": round(cvd, 4),
                "price_drop_pct": round(price_drop_pct, 2),
            },
        )
    return None


def _evaluate_cvd_rvol_compression(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    candles_15m = _confirmed_candles(list(state.candles.get("15", [])))
    candles_1h = _confirmed_candles(list(state.candles.get("60", [])))
    ma_length = int(settings.get("volume_ma_length", 20))
    if len(candles_15m) < ma_length + 1 or len(candles_1h) < 12:
        return None

    latest = candles_15m[-1]
    volume_avg = _avg_volume(candles_15m[-ma_length - 1 : -1])
    if volume_avg <= 0:
        return None

    rvol = latest.volume / volume_avg
    compression = _range_pct(candles_1h[-int(settings.get("compression_lookback_hours", 24)) :])
    cvd_15m = state.cvd_since(15 * 60 * 1000)
    cvd_ratio = cvd_15m / volume_avg
    confirmed_rule = cluster.rule

    if (
        compression <= float(settings.get("compression_max_range_pct", 8.0))
        and rvol >= float(settings.get("volume_multiplier", 3.0))
        and cvd_ratio >= float(settings.get("min_positive_cvd_ratio", 0.35))
        and state.can_alert(confirmed_rule, float(settings.get("cooldown_minutes", 30)))
    ):
        state.mark_alert(confirmed_rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price,
            title=f"Volume institucional detectado em {state.symbol}",
            message=(
                f"Atencao: volume comprador anomalo entrou em {state.symbol}. "
                "Possivel rompimento em formacao; verifique entrada manual."
            ),
            metrics={
                "price": state.price,
                "rvol_15m": round(rvol, 2),
                "cvd_15m": round(cvd_15m, 4),
                "cvd_ratio": round(cvd_ratio, 2),
                "range_24h_pct": round(compression, 2),
            },
        ))
    return None


def _evaluate_wave_surf_entry(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    if cluster.exchange != "bybit_linear":
        return None
    if _is_stable_pair(state.symbol):
        return None
    if not bool(cluster.settings.get("wave_surf_enabled", True)):
        return None
    if not state.can_alert(cluster.rule, float(cluster.settings.get("cooldown_minutes", 20))):
        return None
    if state.price is None or state.change_24h_pct is None or state.range_24h_pct is None or state.turnover_24h is None:
        return None

    min_change = float(cluster.settings.get("wave_surf_min_change_24h_pct", 6.0))
    min_range = float(cluster.settings.get("wave_surf_min_range_24h_pct", 4.0))
    min_turnover = float(cluster.settings.get("wave_surf_min_turnover_24h", _default_min_turnover(cluster)))
    if cluster.id.startswith("bybit_hot_momentum"):
        min_change = float(cluster.settings.get("wave_surf_min_change_24h_pct", 8.0))

    if (
        state.change_24h_pct >= min_change
        and state.range_24h_pct >= min_range
        and state.turnover_24h >= min_turnover
    ):
        stop_pct = _trailing_stop_pct(cluster)
        state.mark_alert(cluster.rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price,
            title=f"Onda forte em andamento em {state.symbol}",
            message=(
                f"{state.symbol} esta surfando uma onda forte agora: variacao 24h alta, range aberto e volume suficiente. "
                f"Entrada agressiva com stop movel de {stop_pct:.2f}% do topo monitorado."
            ),
            metrics={
                "price": round(state.price, 8),
                "wave_surf_signal": "sim",
                "explosion_signal": "sim",
                "change_24h_pct": round(state.change_24h_pct, 2),
                "range_24h_pct": round(state.range_24h_pct, 2),
                "turnover_24h_usd": round(state.turnover_24h, 2),
                "peak_price": round(state.price, 8),
                "trailing_stop_pct": round(stop_pct, 2),
                "trailing_stop_price": round(state.price * (1.0 - stop_pct / 100.0), 8),
            },
        ))
    return None


def _evaluate_orderbook_imbalance_vwap(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    candles_1h = _confirmed_candles(list(state.candles.get("60", [])))
    if len(candles_1h) < 24:
        return None

    book = state.orderbook
    if book.ask_notional_band <= 0 or book.bid_notional_band <= 0:
        return None

    bid_ask_ratio = book.bid_notional_band / book.ask_notional_band
    ask_drop_pct = 0.0
    if book.previous_ask_notional_band > 0:
        ask_drop_pct = max(0.0, 100.0 * (book.previous_ask_notional_band - book.ask_notional_band) / book.previous_ask_notional_band)

    weekly_vwap = _vwap(candles_1h[-int(settings.get("vwap_lookback_hours", 168)) :])
    crossed_vwap = state.price is not None and weekly_vwap > 0 and state.price > weekly_vwap
    vwap_distance_pct = 100.0 * ((state.price or 0.0) - weekly_vwap) / weekly_vwap if weekly_vwap > 0 else 999.0
    not_overextended = vwap_distance_pct <= float(settings.get("max_vwap_distance_pct", 2.0))
    confirmed_rule = cluster.rule

    explosion_alert = _evaluate_momentum_explosion(
        state=state,
        cluster=cluster,
        confirmed_rule=confirmed_rule,
        bid_ask_ratio=bid_ask_ratio,
        weekly_vwap=weekly_vwap,
        vwap_distance_pct=vwap_distance_pct,
    )
    if explosion_alert is not None:
        return explosion_alert

    if (
        bid_ask_ratio >= float(settings.get("min_bid_ask_ratio", 1.6))
        and ask_drop_pct >= float(settings.get("sell_wall_drop_pct", 45.0))
        and crossed_vwap
        and not_overextended
        and state.can_alert(confirmed_rule, float(settings.get("cooldown_minutes", 30)))
    ):
        state.mark_alert(confirmed_rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price or 0.0,
            title=f"Livro abriu caminho para alta em {state.symbol}",
            message=(
                f"Atencao: liquidez vendedora sumiu em {state.symbol} e o preco esta acima da VWAP. "
                "Possivel rompimento em formacao; verifique entrada manual."
            ),
            metrics={
                "price": state.price or 0.0,
                "bid_ask_ratio_2pct": round(bid_ask_ratio, 2),
                "ask_drop_pct": round(ask_drop_pct, 2),
                "weekly_vwap": round(weekly_vwap, 8),
                "distance_to_vwap_pct": round(vwap_distance_pct, 2),
            },
        ))
    return None


def _evaluate_momentum_explosion(
    state: SymbolState,
    cluster: ClusterConfig,
    confirmed_rule: str,
    bid_ask_ratio: float,
    weekly_vwap: float,
    vwap_distance_pct: float,
) -> Alert | None:
    settings = cluster.settings
    if not bool(settings.get("momentum_explosion_enabled", False)):
        return None
    if not state.can_alert(confirmed_rule, float(settings.get("cooldown_minutes", 30))):
        return None
    if state.change_24h_pct is None or state.range_24h_pct is None or state.turnover_24h is None:
        return None

    if (
        state.change_24h_pct >= float(settings.get("explosion_min_change_24h_pct", 10.0))
        and state.range_24h_pct >= float(settings.get("explosion_min_range_24h_pct", 8.0))
        and state.turnover_24h >= float(settings.get("explosion_min_turnover_24h", 250_000.0))
        and bid_ask_ratio >= float(settings.get("explosion_min_bid_ask_ratio", 0.95))
        and vwap_distance_pct <= float(settings.get("explosion_max_vwap_distance_pct", 18.0))
    ):
        state.mark_alert(confirmed_rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price or 0.0,
            title=f"Explosao de momentum em {state.symbol}",
            message=(
                f"{state.symbol} acelerou forte nas ultimas 24h com volume suficiente para monitoramento. "
                "Entrada agressiva somente se o grafico confirmar continuidade e nao devolver o movimento."
            ),
            metrics={
                "price": state.price or 0.0,
                "explosion_signal": "sim",
                "change_24h_pct": round(state.change_24h_pct, 2),
                "range_24h_pct": round(state.range_24h_pct, 2),
                "turnover_24h_usd": round(state.turnover_24h, 2),
                "bid_ask_ratio_2pct": round(bid_ask_ratio, 2),
                "weekly_vwap": round(weekly_vwap, 8),
                "distance_to_vwap_pct": round(vwap_distance_pct, 2),
            },
        ))
    return None


def _evaluate_support_absorption_reversal(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    higher_timeframe = str(settings.get("higher_timeframe", "240"))
    reversal_timeframe = str(settings.get("reversal_timeframe", "60"))
    higher_candles = _confirmed_candles(list(state.candles.get(higher_timeframe, [])))
    reversal_candles = _confirmed_candles(list(state.candles.get(reversal_timeframe, [])))
    daily_candles = _confirmed_candles(list(state.candles.get("D", [])))
    volume_ma_length = int(settings.get("volume_ma_length", 20))
    support_lookback = int(settings.get("support_lookback_candles", 30))

    if len(higher_candles) < max(support_lookback, volume_ma_length + 2) or len(reversal_candles) < 3:
        return None

    support_window = higher_candles[-support_lookback:]
    support = min(candle.low for candle in support_window)
    last_higher = higher_candles[-1]
    previous_higher = higher_candles[-2]
    volume_avg = _avg_volume(higher_candles[-volume_ma_length - 1 : -1])
    if support <= 0 or volume_avg <= 0:
        return None

    support_tolerance_pct = float(settings.get("support_tolerance_pct", 1.5))
    distance_from_support_pct = 100.0 * (last_higher.close - support) / support
    touched_support = last_higher.low <= support * (1.0 + support_tolerance_pct / 100.0)
    defended_support = last_higher.close >= support
    sell_volume_spike = last_higher.volume >= volume_avg * float(settings.get("sell_volume_multiplier", 2.0))
    pressure_down = last_higher.close < previous_higher.close

    latest_reversal = reversal_candles[-1]
    previous_reversal = reversal_candles[-2]
    reversal_body_pct = 100.0 * abs(latest_reversal.close - latest_reversal.open) / latest_reversal.open if latest_reversal.open > 0 else 0.0
    positive_reversal = (
        latest_reversal.close > latest_reversal.open
        and latest_reversal.close > previous_reversal.close
        and reversal_body_pct >= float(settings.get("min_reversal_body_pct", 0.15))
    )
    daily_distance_ok = True
    lower_band = 0.0
    if len(daily_candles) >= 20:
        lower_band, _, _ = _bollinger_bands(daily_candles[-20:])
        if lower_band > 0 and state.price is not None:
            daily_distance_ok = 100.0 * abs(state.price - lower_band) / lower_band <= float(
                settings.get("max_daily_distance_from_support_pct", 6.0)
            )

    if (
        touched_support
        and defended_support
        and sell_volume_spike
        and pressure_down
        and positive_reversal
        and daily_distance_ok
        and distance_from_support_pct <= float(settings.get("max_daily_distance_from_support_pct", 6.0))
        and state.can_alert(cluster.rule, float(settings.get("cooldown_minutes", 240)))
    ):
        state.mark_alert(cluster.rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price or latest_reversal.close,
            title=f"Absorcao em suporte detectada em {state.symbol}",
            message=(
                f"Atencao: {state.symbol} segurou suporte com volume vendedor alto e iniciou reversao no 1H. "
                "Possivel acumulacao longa; verifique entrada manual."
            ),
            metrics={
                "price": round(state.price or latest_reversal.close, 8),
                "support": round(support, 8),
                "distance_from_support_pct": round(distance_from_support_pct, 2),
                "volume_vs_avg": round(last_higher.volume / volume_avg, 2),
                "reversal_body_pct": round(reversal_body_pct, 2),
                "daily_lower_band": round(lower_band, 8),
            },
        ))
    return None


def _evaluate_microcap_spread_volume_anomaly(state: SymbolState, cluster: ClusterConfig) -> Alert | None:
    settings = cluster.settings
    timeframe = str(settings.get("volume_timeframe", "15"))
    candles = _confirmed_candles(list(state.candles.get(timeframe, [])))
    volume_ma_length = int(settings.get("volume_ma_length", 16))
    if len(candles) < volume_ma_length + 1 or len(state.spread_samples) < int(settings.get("min_spread_samples", 6)):
        return None

    latest = candles[-1]
    volume_avg = _avg_volume(candles[-volume_ma_length - 1 : -1])
    if volume_avg <= 0 or state.price is None:
        return None

    book = state.orderbook
    if not book.bids or not book.asks:
        return None

    best_bid = max(book.bids)
    best_ask = min(book.asks)
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None

    spread_pct = 100.0 * (best_ask - best_bid) / mid
    average_spread = state.average_spread(exclude_latest=True)
    if average_spread <= 0:
        return None

    rvol = latest.volume / volume_avg
    cvd_window_minutes = int(settings.get("cvd_window_minutes", 15))
    cvd = state.cvd_since(cvd_window_minutes * 60 * 1000)
    cvd_ratio = cvd / volume_avg
    positive_candle = latest.close >= latest.open
    spread_compressed = spread_pct <= average_spread * float(settings.get("spread_compression_ratio", 0.55))

    if (
        spread_pct <= float(settings.get("max_spread_pct", 1.2))
        and spread_compressed
        and rvol >= float(settings.get("volume_multiplier", 5.0))
        and cvd_ratio >= float(settings.get("min_positive_cvd_ratio", 0.25))
        and positive_candle
        and state.can_alert(cluster.rule, float(settings.get("cooldown_minutes", 45)))
    ):
        state.mark_alert(cluster.rule)
        return _activate_entry(state, cluster, Alert(
            symbol=state.symbol,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            rule=cluster.rule,
            price=state.price,
            title=f"Microcap com injecao de volume em {state.symbol}",
            message=(
                f"Atencao: spread apertou e entrou volume comprador anomalo em {state.symbol}. "
                "Possivel inicio de FOMO/manipulacao; verifique liquidez e entrada manual."
            ),
            metrics={
                "price": round(state.price, 8),
                "spread_pct": round(spread_pct, 3),
                "avg_spread_pct": round(average_spread, 3),
                "rvol": round(rvol, 2),
                "cvd": round(cvd, 4),
                "cvd_ratio": round(cvd_ratio, 2),
            },
        ))
    return None


def _quality_gate_entry_alert(
    state: SymbolState,
    cluster: ClusterConfig,
    alert: Alert,
    market_state: MarketState | None,
) -> Alert | None:
    score, notes, regime = _entry_quality_score(state, cluster, alert, market_state)
    is_explosion = alert.metrics.get("explosion_signal") == "sim"
    is_wave_surf = alert.metrics.get("wave_surf_signal") == "sim"
    min_score = float(
        cluster.settings.get(
            "wave_surf_quality_min_score" if is_wave_surf else "explosion_quality_min_score" if is_explosion else "quality_min_score",
            25.0 if is_wave_surf else 45.0 if is_explosion else _default_quality_min_score(cluster),
        )
    )
    min_turnover_key = "wave_surf_min_turnover_24h" if is_wave_surf else "min_turnover_24h"
    min_turnover = float(cluster.settings.get(min_turnover_key, _default_min_turnover(cluster)))

    if _is_stable_pair(state.symbol):
        state.deactivate_signal(cluster.rule)
        return None
    if state.turnover_24h is not None and state.turnover_24h < min_turnover:
        state.deactivate_signal(cluster.rule)
        return None
    if regime == "risk_off" and not is_explosion and not cluster.id.startswith("bybit_reversal") and score < 90:
        state.deactivate_signal(cluster.rule)
        return None
    if score < min_score:
        state.deactivate_signal(cluster.rule)
        return None

    enriched_metrics = {
        **alert.metrics,
        "quality_score": round(score, 1),
        "market_regime": regime,
        "quality_notes": "; ".join(notes[:4]),
    }
    if state.change_24h_pct is not None:
        enriched_metrics["change_24h_pct"] = round(state.change_24h_pct, 2)
    if state.range_24h_pct is not None:
        enriched_metrics["range_24h_pct"] = round(state.range_24h_pct, 2)
    if state.turnover_24h is not None:
        enriched_metrics["turnover_24h_usd"] = round(state.turnover_24h, 2)
    stop_pct = _trailing_stop_pct(cluster)
    if stop_pct > 0:
        peak_price = max(alert.price, state.price or alert.price)
        enriched_metrics["peak_price"] = round(peak_price, 8)
        enriched_metrics["trailing_stop_pct"] = round(stop_pct, 2)
        enriched_metrics["trailing_stop_price"] = round(peak_price * (1.0 - stop_pct / 100.0), 8)

    state.activate_signal(cluster.rule, alert.price, "CONFIRMADO", enriched_metrics)
    return Alert(
        symbol=alert.symbol,
        cluster_id=alert.cluster_id,
        cluster_name=alert.cluster_name,
        rule=alert.rule,
        price=alert.price,
        title=alert.title,
        message=alert.message,
        metrics=enriched_metrics,
    )


def _entry_quality_score(
    state: SymbolState,
    cluster: ClusterConfig,
    alert: Alert,
    market_state: MarketState | None,
) -> tuple[float, list[str], str]:
    score = 0.0
    notes: list[str] = []
    turnover = state.turnover_24h or 0.0
    change = state.change_24h_pct
    range_pct = state.range_24h_pct

    if turnover >= 20_000_000:
        score += 25
        notes.append("liquidez alta")
    elif turnover >= 5_000_000:
        score += 20
        notes.append("liquidez boa")
    elif turnover >= 1_000_000:
        score += 12
        notes.append("liquidez media")
    elif turnover >= 250_000:
        score += 5
        notes.append("liquidez baixa")
    else:
        score -= 15
        notes.append("liquidez fraca")

    if change is not None:
        if cluster.id.startswith("bybit_hot_momentum"):
            if 8 <= change <= 45:
                score += 25
                notes.append("momentum forte sem excesso extremo")
            elif change > 45:
                score += 8
                notes.append("muito esticada")
            elif change < 0:
                score -= 20
        elif cluster.id.startswith("bybit_reversal"):
            if change <= -6:
                score += 18
                notes.append("queda forte para reversao")
            elif change > 5:
                score -= 10
        elif cluster.rule == "cvd_rvol_compression":
            if abs(change) <= 4:
                score += 15
                notes.append("preco ainda comprimido")
            elif change > 12:
                score -= 12
        elif change > 0:
            score += 10

    if range_pct is not None:
        if cluster.rule == "cvd_rvol_compression" and range_pct <= 8:
            score += 18
            notes.append("range curto")
        elif range_pct > 45:
            score -= 12
            notes.append("volatilidade exagerada")
        elif 6 <= range_pct <= 25:
            score += 8

    score += _rule_specific_quality(alert)
    regime = _market_regime(market_state)
    if regime == "bull":
        score += 8
        notes.append("BTC favorece risco")
    elif regime == "risk_off":
        score -= 22
        notes.append("BTC em modo risco")
    else:
        notes.append("BTC neutro")

    if cluster.id.startswith("bybit_low_liquidity"):
        score -= 12
        notes.append("cluster de baixa liquidez")

    return max(0.0, min(score, 100.0)), notes, regime


def _rule_specific_quality(alert: Alert) -> float:
    score = 0.0
    bid_ask_ratio = _metric_float(alert.metrics.get("bid_ask_ratio_2pct"))
    ask_drop_pct = _metric_float(alert.metrics.get("ask_drop_pct"))
    rvol = _metric_float(alert.metrics.get("rvol_15m") or alert.metrics.get("rvol"))
    cvd_ratio = _metric_float(alert.metrics.get("cvd_ratio"))
    volume_vs_avg = _metric_float(alert.metrics.get("volume_vs_avg"))

    if bid_ask_ratio is not None:
        score += min(22.0, max(0.0, (bid_ask_ratio - 1.0) * 18.0))
    if ask_drop_pct is not None:
        score += min(16.0, ask_drop_pct / 3.0)
    if rvol is not None:
        score += min(18.0, rvol * 4.0)
    if cvd_ratio is not None:
        score += min(16.0, max(0.0, cvd_ratio) * 25.0)
    if volume_vs_avg is not None:
        score += min(16.0, volume_vs_avg * 5.0)
    if alert.rule == "support_absorption_reversal":
        score += 14.0
    if alert.metrics.get("explosion_signal") == "sim":
        score += 45.0
    return score


def _market_regime(market_state: MarketState | None) -> str:
    if market_state is None:
        return "neutral"
    btc = market_state.get("BTCUSDT")
    if btc is None:
        return "neutral"
    change = btc.change_24h_pct
    if change is None:
        return "neutral"
    if change <= -2.5:
        return "risk_off"
    if change >= 1.0:
        return "bull"
    return "neutral"


def _default_quality_min_score(cluster: ClusterConfig) -> float:
    if cluster.id.startswith("bybit_low_liquidity"):
        return 86.0
    if cluster.id.startswith("bybit_hot_momentum"):
        return 70.0
    if cluster.id.startswith("bybit_reversal"):
        return 74.0
    return 72.0


def _default_min_turnover(cluster: ClusterConfig) -> float:
    if cluster.id.startswith("bybit_low_liquidity"):
        return 75_000.0
    if cluster.exchange == "bybit_linear":
        return 300_000.0
    return 0.0


def _trailing_stop_pct(cluster: ClusterConfig) -> float:
    default = 0.5 if cluster.exchange == "bybit_linear" else 0.0
    return float(cluster.settings.get("trailing_stop_pct", default))


def _is_stable_pair(symbol: str) -> bool:
    return symbol in {"USDCUSDT", "USDEUSDT", "USD1USDT", "RLUSDUSDT"}


def _metric_float(value: float | str | None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _avg_volume(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.volume for candle in candles) / len(candles)


def _confirmed_candles(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.confirmed]


def _range_pct(candles: list[Candle]) -> float:
    if not candles:
        return 999.0
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    close = candles[-1].close
    if close <= 0:
        return 999.0
    return 100.0 * (high - low) / close


def _vwap(candles: list[Candle]) -> float:
    notional = 0.0
    volume = 0.0
    for candle in candles:
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        notional += typical_price * candle.volume
        volume += candle.volume
    if volume <= 0:
        return 0.0
    return notional / volume


def _bollinger_bands(candles: list[Candle], deviations: float = 2.0) -> tuple[float, float, float]:
    closes = [candle.close for candle in candles]
    if not closes:
        return 0.0, 0.0, 0.0
    middle = sum(closes) / len(closes)
    variance = sum((close - middle) ** 2 for close in closes) / len(closes)
    std = variance**0.5
    return middle - deviations * std, middle, middle + deviations * std
