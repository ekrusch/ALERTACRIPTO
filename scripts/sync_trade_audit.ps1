# Copia trade_audit.jsonl do Hetzner para storage/ (mesmo padrao de sync_status.ps1).
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $repoRoot "app.py"))) {
    throw "app.py nao encontrado; repo em $repoRoot"
}
$destDir = Join-Path $repoRoot "storage"
$dest = Join-Path $destDir "trade_audit.jsonl"
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
$remote = "root@178.104.166.222:/opt/radar-cripto/storage/trade_audit.jsonl"
Write-Host "Baixando $remote -> $dest"
scp -o BatchMode=yes $remote $dest
Write-Host "OK"
