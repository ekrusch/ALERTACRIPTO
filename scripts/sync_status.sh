#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO/storage/status.json"
mkdir -p "$REPO/storage"
echo "Baixando root@178.104.166.222:/opt/radar-cripto/storage/status.json -> $DEST"
scp -o BatchMode=yes root@178.104.166.222:/opt/radar-cripto/storage/status.json "$DEST"
echo "OK"
