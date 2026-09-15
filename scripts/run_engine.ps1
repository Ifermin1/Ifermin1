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
# Abre el puerto en el firewall de Windows para que el teléfono (misma Wi-Fi) pueda entrar.
$port = 8000
if (Test-Path ".env") { $m = Select-String -Path .env -Pattern '^API_PORT=(\d+)' ; if ($m) { $port = [int]$m.Matches[0].Groups[1].Value } }
if (-not (Get-NetFirewallRule -DisplayName "TradePilot X" -ErrorAction SilentlyContinue)) {
    try {
        New-NetFirewallRule -DisplayName "TradePilot X" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Private | Out-Null
        Write-Host "Regla de firewall creada para el puerto $port (redes privadas)." -ForegroundColor Green
    } catch {
        Write-Host "No se pudo crear la regla de firewall (ejecuta PowerShell como administrador una vez, o abre el puerto $port a mano)." -ForegroundColor Yellow
    }
}

.\.venv\Scripts\python -m tradepilot.main
