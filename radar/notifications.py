from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request

from dotenv import load_dotenv

from radar.engine.rules import Alert

load_dotenv()


class Notifier:
    async def send(self, alert: Alert) -> None:
        text = _format_alert(alert)
        tasks = []
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            tasks.append(asyncio.to_thread(_send_telegram, text))
        if os.getenv("DISCORD_WEBHOOK_URL"):
            tasks.append(asyncio.to_thread(_send_discord, text))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"Falha ao enviar notificacao: {result}", flush=True)
        print(text, flush=True)


def _format_alert(alert: Alert) -> str:
    tradingview_url = _tradingview_url(alert)
    trade_url = _exchange_trade_url(alert)
    explanation = _human_explanation(alert)
    metrics = "\n".join(f"- {label}: {value}" for label, value in _friendly_metrics(alert).items())
    entry_price = _entry_price_text(alert)
    level = _alert_level(alert)
    parts = [
        f"ALERTA {level}: {alert.symbol} chamou atencao",
        "",
        explanation,
        "",
        f"Preço agora: {_fmt_price(alert.price)}",
    ]
    if _is_exit(alert):
        parts.append(f"Ação sugerida: {_exit_action_text(alert)}")
    else:
        parts.append(f"Entrada de referência: {entry_price}")
    if _is_preparing(alert):
        parts.extend(["", "Para entrar, observe exatamente isso:", _entry_checklist(alert)])
    parts.extend(
        [
            "",
            "O que eu observei:",
            metrics,
            "",
            f"Gráfico para conferir: {tradingview_url}",
            f"Operar na corretora: {trade_url}",
            "",
            "Importante: isso é um alerta de oportunidade, não ordem automática. Olhe o gráfico antes de entrar.",
        ]
    )
    return "\n".join(parts)


def _human_explanation(alert: Alert) -> str:
    if _is_exit(alert):
        return (
            f"{alert.message} Se voce entrou ou estava pensando em entrar, agora e hora de proteger capital: "
            "reduzir risco, sair parcial/total ou esperar o grafico recuperar antes de fazer qualquer coisa."
        )
    if alert.rule == "cvd_rvol_compression":
        rvol = alert.metrics.get("rvol_15m", "?")
        range_pct = alert.metrics.get("range_24h_pct", "?")
        return (
            "Parece que entrou compra forte depois de um período de preço mais travado. "
            f"O volume veio cerca de {rvol}x acima do normal e o ativo ainda está em uma faixa curta de {range_pct}% no dia. "
            "Esse tipo de movimento pode ser começo de rompimento, quando alguém maior para de acumular devagar e começa a comprar a mercado."
        )
    if alert.rule == "cvd_rvol_compression_preparing":
        rvol = alert.metrics.get("rvol_15m", "?")
        range_pct = alert.metrics.get("range_24h_pct", "?")
        return (
            "Ainda nao e rompimento confirmado. O que apareceu foi o comeco de uma movimentacao: "
            f"o volume ja esta {rvol}x acima do normal e o preco ainda esta relativamente preso em uma faixa de {range_pct}%. "
            "Esse e o tipo de aviso para voce abrir o grafico antes da explosao, nao para entrar cego."
        )
    if alert.rule == "orderbook_imbalance_vwap":
        ratio = alert.metrics.get("bid_ask_ratio_2pct", "?")
        ask_drop = alert.metrics.get("ask_drop_pct", "?")
        return (
            "O livro ficou mais leve para cima. Tinha menos venda segurando o preço e as compras ficaram mais fortes perto do valor atual. "
            f"A força compradora no book está em {ratio}x e a parede de venda caiu cerca de {ask_drop}%. "
            "Se o gráfico confirmar, pode ser uma tentativa de arrancada."
        )
    if alert.rule == "orderbook_imbalance_vwap_preparing":
        ratio = alert.metrics.get("bid_ask_ratio_2pct", "?")
        ask_drop = alert.metrics.get("ask_drop_pct", "?")
        return (
            "Esse e um aviso antecipado. O preco ainda pode nao ter rompido, mas o livro ja comecou a melhorar para alta: "
            f"as compras estao {ratio}x mais fortes que as vendas proximas e a parede de venda encolheu cerca de {ask_drop}%. "
            "A ideia aqui e te chamar para olhar antes do candle esticar."
        )
    if alert.rule == "support_absorption_reversal":
        support = _fmt_price(alert.metrics.get("support"))
        volume = alert.metrics.get("volume_vs_avg", "?")
        return (
            "O preço caiu até uma região de suporte, veio volume vendedor forte, mas mesmo assim não conseguiu perder a região. "
            f"Isso sugere absorção: alguém pode estar comprando tudo que aparece. O suporte observado ficou perto de {support}, "
            f"com volume cerca de {volume}x acima da média. O alerta saiu no primeiro sinal de reação."
        )
    if alert.rule == "support_absorption_reversal_preparing":
        support = _fmt_price(alert.metrics.get("support"))
        volume = alert.metrics.get("volume_vs_avg", "?")
        return (
            "Esse e um aviso de suporte sendo defendido. O preco chegou perto de uma regiao importante, apareceu volume, "
            f"mas ainda falta confirmacao forte de reversao. O suporte observado esta perto de {support}, "
            f"com volume em torno de {volume}x a media."
        )
    if alert.rule == "microcap_spread_volume_anomaly":
        spread = alert.metrics.get("spread_pct", "?")
        rvol = alert.metrics.get("rvol", "?")
        return (
            "Essa é uma microcap, então o sinal é mais agressivo e precisa de cuidado. "
            f"O spread apertou para cerca de {spread}% e entrou volume anormal, perto de {rvol}x a média. "
            "Esse padrão pode aparecer no começo de uma puxada de FOMO, mas também pode ser manipulação. Entre só se o gráfico confirmar."
        )
    if alert.rule == "microcap_spread_volume_anomaly_preparing":
        spread = alert.metrics.get("spread_pct", "?")
        rvol = alert.metrics.get("rvol", "?")
        return (
            "Aviso antecipado em microcap. O spread comecou a apertar e o volume ja subiu, "
            f"mas ainda nao chegou no gatilho forte. Spread perto de {spread}% e volume em {rvol}x a media. "
            "Serve para observar rapido, porque microcap pode andar em poucos minutos."
        )
    return alert.message


def _entry_price_text(alert: Alert) -> str:
    if _is_preparing(alert):
        return f"ainda nao e entrada pronta; observe perto de {_fmt_price(alert.price)} e espere confirmacao"
    if alert.rule == "support_absorption_reversal" and alert.metrics.get("support"):
        return f"entre {_fmt_price(alert.metrics.get('support'))} e {_fmt_price(alert.price)}, se o candle confirmar força"
    if alert.rule == "microcap_spread_volume_anomaly":
        return f"perto do preço atual ({_fmt_price(alert.price)}), somente se o spread continuar apertado"
    return f"perto do preço atual ({_fmt_price(alert.price)}), depois de confirmar o rompimento no gráfico"


def _exit_action_text(alert: Alert) -> str:
    if alert.rule == "support_absorption_reversal_exit":
        return "se entrou pela defesa de suporte, considere sair se o preço continuar abaixo do suporte."
    if alert.rule == "orderbook_imbalance_vwap_exit":
        return "se entrou no rompimento, considere sair ou reduzir se o preço perdeu a VWAP/book virou contra."
    if alert.rule == "microcap_spread_volume_anomaly_exit":
        return "em microcap, nao insiste: se spread abriu ou compra sumiu, considere sair rapido."
    if alert.rule == "cvd_rvol_compression_exit":
        return "se a compra virou venda e o preço devolveu, considere sair ou esperar novo rompimento."
    return "considere reduzir risco e esperar nova confirmacao."


def _entry_checklist(alert: Alert) -> str:
    if alert.rule == "cvd_rvol_compression_preparing":
        return "\n".join(
            [
                "- O preço precisa romper a máxima do range/última resistência no 15m.",
                "- O candle de rompimento precisa fechar forte, de preferência perto da máxima.",
                "- O volume precisa continuar acima da média; se o volume morrer, ignora.",
                "- Evite entrar se o preço romper e voltar rápido para dentro do range.",
                f"- Entrada mais segura: acima da resistência confirmada, não simplesmente porque o alerta tocou em {_fmt_price(alert.price)}.",
            ]
        )
    if alert.rule == "orderbook_imbalance_vwap_preparing":
        return "\n".join(
            [
                "- O preço precisa continuar acima da VWAP ou romper ela com candle forte.",
                "- A região rompida precisa segurar no pullback; se voltar abaixo da VWAP, perde força.",
                "- O book precisa continuar comprador: compras maiores que vendas perto do preço.",
                "- Evite comprar candle muito esticado; prefira rompimento confirmado ou pullback segurando.",
                f"- Entrada mais segura: rompimento/pullback acima da VWAP, com preço ainda perto de {_fmt_price(alert.price)}.",
            ]
        )
    if alert.rule == "support_absorption_reversal_preparing":
        support = _fmt_price(alert.metrics.get("support"))
        return "\n".join(
            [
                f"- O suporte perto de {support} precisa continuar segurando.",
                "- Espere um candle de 1H fechar verde ou fazer fundo mais alto.",
                "- Se cair abaixo do suporte com volume vendedor, cancela a ideia.",
                "- Melhor entrada: perto do suporte, depois de mostrar reação, não no meio de candle aleatório.",
                f"- Região de interesse: entre {support} e {_fmt_price(alert.price)}, se aparecer confirmação.",
            ]
        )
    if alert.rule == "microcap_spread_volume_anomaly_preparing":
        return "\n".join(
            [
                "- O spread precisa continuar apertado; se abrir demais, não entra.",
                "- O volume comprador precisa aumentar, não apenas dar um pico isolado.",
                "- O preço precisa romper a máxima curta no 1m/5m/15m e não devolver imediatamente.",
                "- Como é microcap, use tamanho menor e aceite que pode ser manipulação.",
                f"- Entrada mais segura: só se romper com volume perto de {_fmt_price(alert.price)} e o book continuar firme.",
            ]
        )
    return "- Espere confirmação no gráfico antes de qualquer entrada."


def _friendly_metrics(alert: Alert) -> dict[str, str]:
    labels = {
        "alert_level": "nivel do alerta",
        "price": "preço",
        "rvol_15m": "volume 15m acima do normal",
        "cvd_15m": "saldo comprador 15m",
        "cvd_ratio": "força compradora vs volume médio",
        "range_24h_pct": "faixa de preço recente",
        "bid_ask_ratio_2pct": "compras vs vendas no book",
        "ask_drop_pct": "queda da parede de venda",
        "distance_to_vwap_pct": "distância até a VWAP",
        "weekly_vwap": "VWAP semanal",
        "support": "suporte observado",
        "distance_from_support_pct": "distância do suporte",
        "volume_vs_avg": "volume vs média",
        "reversal_body_pct": "força do candle de reação",
        "daily_lower_band": "banda inferior diária",
        "spread_pct": "spread atual",
        "avg_spread_pct": "spread médio recente",
        "rvol": "volume acima do normal",
        "cvd": "saldo comprador",
        "price_drop_pct": "queda desde o alerta",
        "lost_vwap": "perdeu VWAP",
    }
    friendly = {}
    for key, value in alert.metrics.items():
        label = labels.get(key, key)
        friendly[label] = _fmt_metric(key, value)
    return friendly


def _is_preparing(alert: Alert) -> bool:
    return alert.rule.endswith("_preparing") or alert.metrics.get("alert_level") == "PREPARANDO"


def _is_exit(alert: Alert) -> bool:
    return alert.rule.endswith("_exit") or alert.metrics.get("alert_level") == "SAIDA"


def _alert_level(alert: Alert) -> str:
    if _is_exit(alert):
        return "SAIDA / RISCO"
    return "PREPARANDO" if _is_preparing(alert) else "CONFIRMADO"


def _fmt_metric(key: str, value: float | str) -> str:
    if isinstance(value, str):
        return value
    if key in {"rvol", "rvol_15m", "volume_vs_avg", "bid_ask_ratio_2pct", "cvd_ratio"}:
        return f"{value}x"
    if "pct" in key:
        return f"{value}%"
    return _fmt_price(value)


def _fmt_price(value: float | str | None) -> str:
    if value is None:
        return "não disponível"
    if isinstance(value, str):
        return value
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def _tradingview_url(alert: Alert) -> str:
    if alert.cluster_id.startswith("mexc_"):
        exchange = "MEXC"
    elif alert.cluster_id.startswith("kucoin_"):
        exchange = "KUCOIN"
    else:
        exchange = "BYBIT"
    return f"https://www.tradingview.com/chart/?symbol={exchange}:{alert.symbol}"


def _exchange_trade_url(alert: Alert) -> str:
    if alert.cluster_id.startswith("mexc_"):
        return f"https://www.mexc.com/exchange/{_symbol_with_separator(alert.symbol, '_')}"
    if alert.cluster_id.startswith("kucoin_"):
        return f"https://www.kucoin.com/trade/{_symbol_with_separator(alert.symbol, '-')}"
    return f"https://www.bybit.com/trade/usdt/{alert.symbol}"


def _symbol_with_separator(symbol: str, separator: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}{separator}USDT"
    return symbol


def _send_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def _send_discord(text: str) -> None:
    url = os.environ["DISCORD_WEBHOOK_URL"]
    data = json.dumps({"content": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "radar-cripto/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()
