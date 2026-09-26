# Actualiza TradePilot X en Windows: código, dependencias, consola web, addon de NinjaTrader y reinicio del engine.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\update.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/5 Descargando la última versión..." -ForegroundColor Cyan
git fetch --all --prune
if ($LASTEXITCODE -ne 0) { Write-Host "git fetch falló; revisa la conexión." -ForegroundColor Red; exit 1 }
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull falló (cambios locales o rama divergente). Prueba: git stash; y vuelve a ejecutar." -ForegroundColor Red; exit 1
}
$branch = git rev-parse --abbrev-ref HEAD
$commit = git log -1 --format="%h %s"
Write-Host "Rama $branch · $commit" -ForegroundColor Green

Write-Host "== 2/5 Dependencias del engine" -ForegroundColor Cyan
Push-Location "$root\engine"
if (-not (Test-Path ".venv")) { python -m venv .venv }
$stamp = ".venv\deps.stamp"
if (-not (Test-Path $stamp) -or (Get-Item "pyproject.toml").LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    .\.venv\Scripts\pip install -e "."
    New-Item -ItemType File -Path $stamp -Force | Out-Null
} else { Write-Host "Dependencias al día." -ForegroundColor Green }
Pop-Location

Write-Host "== 3/5 Consola web (compilar siempre tras actualizar)" -ForegroundColor Cyan
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location "$root\web"
    if (-not (Test-Path "node_modules") -or (Get-Item "package-lock.json").LastWriteTime -gt (Get-Item "node_modules").LastWriteTime) { npm install --no-audit --no-fund }
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "La compilación de la consola falló; revisa la salida de arriba." -ForegroundColor Red; exit 1 }
    Pop-Location
    Write-Host "Consola compilada: web\dist ($((Get-Item "$root\web\dist\index.html").LastWriteTime))" -ForegroundColor Green
} else {
    Write-Host "npm no está instalado: la consola NO se ha actualizado. Instala Node.js (https://nodejs.org, LTS) y vuelve a ejecutar." -ForegroundColor Red
}

Write-Host "== 4/5 Addon de NinjaTrader" -ForegroundColor Cyan
$docs = [Environment]::GetFolderPath("MyDocuments")   # respeta la redirección a OneDrive
$addonDir = Join-Path $docs "NinjaTrader 8\bin\Custom\AddOns"
$src = Join-Path $root "ninjatrader\TradePilotXBridge.cs"
$dst = Join-Path $addonDir "TradePilotXBridge.cs"
$ver = (Select-String -Path $src -Pattern 'BridgeVersion = "([^"]+)"').Matches[0].Groups[1].Value
if (-not (Test-Path $addonDir)) {
    Write-Host "No encuentro la carpeta de AddOns de NinjaTrader ($addonDir). Copia a mano ninjatrader\TradePilotXBridge.cs." -ForegroundColor Yellow
} elseif ((Test-Path $dst) -and ((Get-FileHash $src).Hash -eq (Get-FileHash $dst).Hash)) {
    Write-Host "El addon v$ver ya está en $dst" -ForegroundColor Green
} else {
    if (Test-Path $dst) {
        $bak = "$dst.bak_" + (Get-Date -Format "yyyyMMdd_HHmmss")
        Copy-Item $dst $bak
        Write-Host "Copia de seguridad del addon anterior: $bak"
    }
    Copy-Item $src $dst -Force
    Write-Host "Addon v$ver copiado a $dst" -ForegroundColor Green
    Write-Host ">> Ahora en NinjaTrader: New -> NinjaScript Editor -> F5 (compilar), y reinicia NinjaTrader." -ForegroundColor Yellow
    Write-Host ">> Debe aparecer en Output: [TradePilotX] Bridge v$ver online" -ForegroundColor Yellow
}

Write-Host "== 5/5 Reiniciando el engine" -ForegroundColor Cyan
$port = 8000
if (Test-Path "$root\engine\.env") { $m = Select-String -Path "$root\engine\.env" -Pattern '^API_PORT=(\d+)'; if ($m) { $port = [int]$m.Matches[0].Groups[1].Value } }
$task = Get-ScheduledTask -TaskName "TradePilotX Engine" -ErrorAction SilentlyContinue
if ($task) { Stop-ScheduledTask -TaskName "TradePilotX Engine" -ErrorAction SilentlyContinue }
# lo que siga escuchando en el puerto (un engine antiguo) se para: si no, seguiría sirviendo la consola vieja
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop; Write-Host "Engine anterior parado (PID $($_.OwningProcess))." } catch { }
}
Start-Sleep -Seconds 1
if ($task) {
    Start-ScheduledTask -TaskName "TradePilotX Engine"
    Write-Host "Engine arrancado como tarea programada. Abre http://localhost:$port y pulsa Ctrl+F5 (o cierra y abre la app en el teléfono)." -ForegroundColor Green
} else {
    Write-Host "Arrancando el engine en esta ventana (Ctrl+C para parar). Abre http://localhost:$port y pulsa Ctrl+F5." -ForegroundColor Green
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run_engine.ps1")
}
