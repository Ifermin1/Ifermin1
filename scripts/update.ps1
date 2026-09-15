# Actualiza TradePilot X en Windows: código, addon de NinjaTrader y arranque del engine.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\update.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/3 Descargando la última versión..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) { Write-Host "git pull falló; revisa la salida de arriba." -ForegroundColor Red; exit 1 }

Write-Host "== 2/3 Addon de NinjaTrader" -ForegroundColor Cyan
$docs = [Environment]::GetFolderPath("MyDocuments")   # respeta la redirección a OneDrive
$addonDir = Join-Path $docs "NinjaTrader 8\bin\Custom\AddOns"
$src = Join-Path $root "ninjatrader\TradePilotXBridge.cs"
$dst = Join-Path $addonDir "TradePilotXBridge.cs"
if (-not (Test-Path $addonDir)) {
    Write-Host "No encuentro la carpeta de AddOns de NinjaTrader ($addonDir). Copia a mano ninjatrader\TradePilotXBridge.cs." -ForegroundColor Yellow
} elseif ((Test-Path $dst) -and ((Get-FileHash $src).Hash -eq (Get-FileHash $dst).Hash)) {
    Write-Host "El addon ya está actualizado en $dst" -ForegroundColor Green
} else {
    if (Test-Path $dst) {
        $bak = "$dst.bak_" + (Get-Date -Format "yyyyMMdd_HHmmss")
        Copy-Item $dst $bak
        Write-Host "Copia de seguridad del addon anterior: $bak"
    }
    Copy-Item $src $dst -Force
    Write-Host "Addon copiado a $dst" -ForegroundColor Green
    Write-Host ">> Ahora en NinjaTrader: New -> NinjaScript Editor -> F5 (compilar), y reinicia NinjaTrader." -ForegroundColor Yellow
    Write-Host ">> Debe aparecer en Output: [TradePilotX] Bridge v1.1 online ... (GET_ACCOUNTS_ALL disponible)" -ForegroundColor Yellow
}

Write-Host "== 3/3 Arrancando el engine (Ctrl+C para parar)" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_engine.ps1")
