# GitHub e Deploy no Servidor

Este projeto deve subir para o GitHub sem segredos. O arquivo `.env` fica somente na sua maquina e no servidor.

## Preparar Git Local

```bash
git init
git add .
git status
git commit -m "Adicionar radar de anomalias cripto"
git branch -M main
```

Depois crie um repositorio vazio no GitHub e conecte:

```bash
git remote add origin https://github.com/SEU_USUARIO/NOME_DO_REPO.git
git push -u origin main
```

## O Que Nao Deve Subir

O `.gitignore` ja bloqueia:

- `.env`
- `.env.*`, exceto `.env.example`
- `.venv/`
- `storage/`
- `__pycache__/`
- `*.pyc`
- logs

Antes de commitar, confira:

```bash
git status --short
```

Se aparecer `.env`, pare e corrija antes de subir.

## Instalar no Servidor Hetzner

Entre no servidor:

```bash
ssh root@178.104.166.222
```

Instale dependencias do sistema:

```bash
apt update
apt install -y git python3 python3-venv python3-pip
```

Clone o projeto:

```bash
git clone https://github.com/SEU_USUARIO/NOME_DO_REPO.git /opt/radar-cripto
cd /opt/radar-cripto
```

Crie ambiente Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Crie o `.env` no servidor:

```bash
nano .env
```

Exemplo:

```env
DISCORD_WEBHOOK_URL=sua_url_do_webhook
RADAR_STATUS_FILE=storage/status.json
```

Teste:

```bash
python -m radar.main
```

## Rodar 24 Horas

Copie o servico:

```bash
cp deploy/radar.service.example /etc/systemd/system/radar.service
systemctl daemon-reload
systemctl enable radar
systemctl start radar
systemctl status radar
```

Ver logs:

```bash
journalctl -u radar -f
```

## Atualizar Codigo no Servidor

Quando voce alterar no Cursor e fizer push:

```bash
cd /opt/radar-cripto
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart radar
```
