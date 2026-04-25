# Radar de Anomalias Cripto

Sistema passivo de monitoramento por WebSocket para detectar anomalias de volume, CVD, livro de ofertas e VWAP.

## Moedas monitoradas nesta primeira versao

- Processo `bybit:bybit_institutional_rwa_defi`: `ONDOUSDT`, `PENDLEUSDT`, `XDCUSDT`, `ZETAUSDT`.
- Processo `bybit:bybit_ai_gpu_directional`: `TAOUSDT`, `RENDERUSDT`, `IOUSDT`, `ARKMUSDT`.
- Processo `bybit:bybit_infra_long_accumulation`: `JASMYUSDT`, `VETUSDT`, `AKTUSDT`, `AIOZUSDT`, `ATHUSDT`.
- Processo `mexc:mexc_infra_long_accumulation_alt`: `OLASUSDT`, `TRACUSDT`.
- Processo `mexc:mexc_microcaps_depin`: `NOSUSDT`, `HONEYUSDT`, `CFGUSDT`.

`OLASUSDT` e `TRACUSDT` nao foram encontrados na Bybit linear nem na KuCoin spot durante a validacao, entao ficaram ativos na MEXC spot.
`DIMOUSDT` nao foi encontrado na MEXC nem na KuCoin durante a validacao; fica pendente para Gate.io/Coinbase.

## Regra do Cluster 3

O cluster de microcaps ignora RSI/MACD e procura:

- spread atual menor que o spread medio recente;
- spread abaixo do limite configurado;
- volume da vela de 15m pelo menos 5x maior que a media das ultimas 16 velas;
- CVD comprador positivo nos ultimos 15 minutos;
- candle atual positivo.

Cada processo imprime no terminal as moedas e o preco atual recebido pelo WebSocket. O painel tambem mostra essa relacao em `Processos / Workers`.

## Como rodar localmente

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m radar.main
```

Em outro terminal:

```bash
streamlit run app.py
```

## Alertas

Preencha no `.env` pelo menos uma opcao:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
```

Se nenhuma opcao estiver preenchida, o alerta aparece apenas no terminal e no arquivo `storage/status.json`.

Cada alerta enviado tambem inclui um link direto do TradingView para apoio visual:

```text
TradingView: https://www.tradingview.com/chart/?symbol=BYBIT:JASMYUSDT
```

O radar continua usando dados diretos das corretoras. O TradingView entra apenas como apoio para abrir o grafico, comparar visualmente e confirmar a leitura antes de qualquer decisao manual.

## Rodar 24 horas em servidor Linux

1. Copie o projeto para `/opt/radar-cripto`.
2. Crie o ambiente virtual e instale as dependencias.
3. Configure o `.env`.
4. Copie `deploy/radar.service.example` para `/etc/systemd/system/radar.service`.
5. Ative:

```bash
sudo systemctl daemon-reload
sudo systemctl enable radar
sudo systemctl start radar
sudo systemctl status radar
```

Logs:

```bash
journalctl -u radar -f
```
