# Arranca el engine de TradePilot X en Windows (junto a NinjaTrader).
# Uso: powershell -ExecutionPolicy Bypass -File scripts\run_engine.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\engine"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\pip install -e "."
}
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Creado engine\.env: edita API_TOKEN y ENGINE_MODE antes de exponerlo." -ForegroundColor Yellow
}
if (-not (Test-Path "..\web\dist\index.html")) {
    Write-Host "No hay build de la consola web; ejecuta 'npm install && npm run build' en web\ (solo API por ahora)." -ForegroundColor Yellow
}
.\.venv\Scripts\python -m tradepilot.main
