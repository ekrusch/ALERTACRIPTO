# Copia status.json do Hetzner para o PC (mesma pasta storage/ do projeto).
# Pode ser executado de qualquer pasta; a raiz do repo e a pasta acima de scripts/
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $repoRoot "app.py"))) {
    throw "app.py nao encontrado; repo em $repoRoot"
}
$destDir = Join-Path $repoRoot "storage"
$dest = Join-Path $destDir "status.json"
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
$remote = "root@178.104.166.222:/opt/radar-cripto/storage/status.json"
Write-Host "Baixando $remote -> $dest"
scp -o BatchMode=yes $remote $dest
Write-Host "OK"
