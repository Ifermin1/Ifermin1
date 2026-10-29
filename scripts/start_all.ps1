# Levanta TODO TradePilot X de una vez: NinjaTrader (con el addon dentro), el engine, el túnel si está instalado y la consola.
#
#   scripts\start_all.bat                   doble clic: lo mismo que abajo, en una ventana que se queda abierta
#   scripts\start_all.ps1                   arranca NinjaTrader si no está abierto, espera al addon, arranca el engine y abre la consola
#   scripts\start_all.ps1 -NinjaPath "D:\NinjaTrader 8\bin\NinjaTrader.exe"   si NinjaTrader no está en la ruta habitual
#   scripts\start_all.ps1 -Tunnel           además arranca la tarea "TradePilotX Tunnel" (la crea scripts\publish.ps1 -Install)
#   scripts\start_all.ps1 -NoBrowser        no abre el navegador al final
#   scripts\start_all.ps1 -NoNinja          solo engine (+túnel): útil en modo mock o si NinjaTrader ya lo abres tú
#   scripts\start_all.ps1 -Install          crea un acceso directo en la carpeta Inicio de Windows: todo arranca solo al iniciar sesión
#   scripts\start_all.ps1 -Uninstall        quita ese acceso directo
#
# Lo que NO puede hacer un script: compilar el addon (una sola vez, en NinjaTrader: New -> NinjaScript Editor -> F5) ni
# teclear la contraseña de NinjaTrader. Marca "Remember password" y "Auto login" en la ventana de entrada de NinjaTrader y
# el addon cargará solo cada vez que NinjaTrader arranque.
param(
    [string]$NinjaPath = "",
    [int]$WaitAddonS = 240,
    [int]$WaitEngineS = 120,
    [switch]$Tunnel,
    [switch]$NoBrowser,
    [switch]$NoNinja,
    [switch]$Install,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root "engine\.env"
$taskEngine = "TradePilotX Engine"
$taskTunnel = "TradePilotX Tunnel"
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "TradePilot X.lnk"

function Read-Env([string]$key, [string]$default) {
    if (Test-Path $envFile) { $m = Select-String -Path $envFile -Pattern "^\s*$key=(.*)$"; if ($m) { $v = $m.Matches[0].Groups[1].Value.Trim(); if ($v) { return $v } } }
    return $default
}
function Test-Port([string]$h, [int]$p, [int]$timeoutMs = 700) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $r = $c.BeginConnect($h, $p, $null, $null); if (-not $r.AsyncWaitHandle.WaitOne($timeoutMs, $false)) { return $false }; $c.EndConnect($r); return $true }
    catch { return $false } finally { $c.Close() }
}
function Wait-Port([string]$h, [int]$p, [int]$seconds) {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $seconds) {
        if (Test-Port $h $p) { Write-Host ""; return $true }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    return $false
}
function Find-Ninja {
    if ($NinjaPath) { return $NinjaPath }
    if ($env:TPX_NINJA -and (Test-Path $env:TPX_NINJA)) { return $env:TPX_NINJA }
    $candidates = @("C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe", "C:\Program Files (x86)\NinjaTrader 8\bin\NinjaTrader.exe", "D:\NinjaTrader 8\bin\NinjaTrader.exe")
    foreach ($base in @([Environment]::GetFolderPath("ProgramFiles"), [Environment]::GetFolderPath("ProgramFilesX86"), $env:LOCALAPPDATA)) {
        if ($base) { $candidates += (Join-Path $base "NinjaTrader 8\bin\NinjaTrader.exe") }
    }
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    # acceso directo del menú Inicio o del escritorio
    $lnks = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:AppData\Microsoft\Windows\Start Menu\Programs", [Environment]::GetFolderPath("Desktop")) |
        Where-Object { Test-Path $_ } | ForEach-Object { Get-ChildItem $_ -Recurse -Filter "NinjaTrader*.lnk" -ErrorAction SilentlyContinue }
    foreach ($l in $lnks) {
        try { $t = (New-Object -ComObject WScript.Shell).CreateShortcut($l.FullName).TargetPath; if ($t -and (Test-Path $t) -and $t -like "*NinjaTrader*.exe") { return $t } } catch { }
    }
    return ""
}

# ---- instalar / quitar el arranque con Windows ----
if ($Uninstall) {
    if (Test-Path $shortcut) { Remove-Item $shortcut -Force; Write-Host "Quitado: $shortcut" -ForegroundColor Green } else { Write-Host "No había acceso directo en la carpeta Inicio." }
    exit 0
}
if ($Install) {
    $opts = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Tunnel) { $opts += "-Tunnel" }; if ($NoBrowser) { $opts += "-NoBrowser" }; if ($NinjaPath) { $opts += "-NinjaPath `"$NinjaPath`"" }
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"; $s.Arguments = ($opts -join " "); $s.WorkingDirectory = $root
    $s.Description = "Arranca NinjaTrader, el engine y la consola de TradePilot X"
    $s.Save()
    Write-Host "Acceso directo creado: $shortcut" -ForegroundColor Green
    Write-Host "A partir de ahora, al iniciar sesión en Windows se abre NinjaTrader, arranca el engine y se abre la consola."
    Write-Host "Si ya tienes la tarea '$taskEngine' (install_autostart.ps1) no pasa nada: el script la usa y no arranca un segundo engine."
    exit 0
}

$port = [int](Read-Env "API_PORT" "8000")
$zmqHost = Read-Env "ZMQ_HOST" "127.0.0.1"
$syncPort = [int](Read-Env "ZMQ_SYNC_PORT" "5557")
$mode = Read-Env "ENGINE_MODE" "mock"
Write-Host "TradePilot X · arranque completo  (modo $mode · engine en :$port · addon en ${zmqHost}:$syncPort)" -ForegroundColor Cyan
if (-not (Test-Path $envFile)) { Write-Host "No existe engine\.env: run_engine.ps1 lo creará con el ejemplo; después edita API_TOKEN y ENGINE_MODE." -ForegroundColor Yellow }
$summary = @()

# ---- 1/4 NinjaTrader + addon ----
if ($NoNinja -or $mode -ne "ninja") {
    if (-not $NoNinja) { Write-Host "== 1/4 NinjaTrader: omitido (ENGINE_MODE=$mode; pon ENGINE_MODE=ninja en engine\.env para operar de verdad)" -ForegroundColor Yellow }
    else { Write-Host "== 1/4 NinjaTrader: omitido (-NoNinja)" -ForegroundColor Yellow }
    $summary += "NinjaTrader: no se ha tocado"
} else {
    Write-Host "== 1/4 NinjaTrader" -ForegroundColor Cyan
    $running = Get-Process -Name "NinjaTrader" -ErrorAction SilentlyContinue
    $launched = $true
    if ($running) {
        Write-Host "NinjaTrader ya está abierto (PID $($running[0].Id))." -ForegroundColor Green
    } else {
        $exe = Find-Ninja
        if (-not $exe) {
            $launched = $false
            Write-Host "No encuentro NinjaTrader.exe. Ábrelo a mano o indica la ruta: scripts\start_all.ps1 -NinjaPath `"C:\...\NinjaTrader.exe`"" -ForegroundColor Red
        } else {
            Write-Host "Abriendo $exe ..."
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) | Out-Null
            Write-Host "Si NinjaTrader pide usuario y contraseña, entra y marca 'Remember password' + 'Auto login' para la próxima vez." -ForegroundColor Yellow
        }
    }
    $wait = if ($launched) { $WaitAddonS } else { 5 }
    Write-Host "Esperando al addon TradePilotXBridge (puerto $syncPort, hasta $wait s)" -NoNewline
    if (Wait-Port $zmqHost $syncPort $wait) {
        Write-Host "Addon en marcha: responde en ${zmqHost}:$syncPort." -ForegroundColor Green
        $summary += "Addon de NinjaTrader: OK"
    } else {
        Write-Host "El addon no responde tras $wait s. Causas habituales:" -ForegroundColor Red
        Write-Host "  - NinjaTrader sigue en la ventana de entrada (contraseña) o cargando: espera y vuelve a ejecutar este script." -ForegroundColor Red
        Write-Host "  - El addon no está compilado: en NinjaTrader, New -> NinjaScript Editor -> F5 y reinicia NinjaTrader (solo la primera vez y tras cada update.ps1 que lo copie)." -ForegroundColor Red
        Write-Host "  - Puertos distintos en Documents\NinjaTrader 8\TradePilotX\config.json y en engine\.env (ZMQ_SYNC_PORT)." -ForegroundColor Red
        Write-Host "  En NinjaTrader, New -> NinjaScript Output debe mostrar '[TradePilotX] Bridge v3.1 online'." -ForegroundColor Red
        $summary += "Addon de NinjaTrader: NO responde (el engine arranca igual y se conectará cuando el addon aparezca)"
    }
}

# ---- 2/4 engine ----
Write-Host "== 2/4 Engine" -ForegroundColor Cyan
if (Test-Port "127.0.0.1" $port) {
    Write-Host "El engine ya está escuchando en :$port." -ForegroundColor Green
    $summary += "Engine: ya estaba en marcha"
} else {
    $task = Get-ScheduledTask -TaskName $taskEngine -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $taskEngine
        Write-Host "Tarea '$taskEngine' arrancada (minimizada; logs en engine\logs\)."
    } else {
        $runner = Join-Path $PSScriptRoot "run_engine.ps1"
        Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$runner`"" -WorkingDirectory $root -WindowStyle Minimized | Out-Null
        Write-Host "Engine arrancado en una ventana minimizada (scripts\run_engine.ps1). Para que arranque con Windows: scripts\install_autostart.ps1 (admin) o scripts\start_all.ps1 -Install."
    }
    Write-Host "Esperando al engine (hasta $WaitEngineS s; la primera vez instala dependencias y compila la consola, puede tardar más)" -NoNewline
    if (Wait-Port "127.0.0.1" $port $WaitEngineS) {
        Write-Host "Engine en marcha en http://localhost:$port" -ForegroundColor Green
        $summary += "Engine: OK"
    } else {
        Write-Host "El engine no responde en :$port. Mira la ventana minimizada del engine o engine\logs\ (y engine\logs\supervisor.log)." -ForegroundColor Red
        $summary += "Engine: NO responde"
    }
}

# ---- 3/4 túnel ----
if ($Tunnel) {
    Write-Host "== 3/4 Túnel" -ForegroundColor Cyan
    $tt = Get-ScheduledTask -TaskName $taskTunnel -ErrorAction SilentlyContinue
    if ($tt) {
        if ($tt.State -eq "Running") { Write-Host "El túnel ya está corriendo." -ForegroundColor Green }
        else { Start-ScheduledTask -TaskName $taskTunnel; Write-Host "Tarea '$taskTunnel' arrancada." -ForegroundColor Green }
        $urlFile = Join-Path $root "engine\data\public_url.txt"
        if (Test-Path $urlFile) { Write-Host ("Consola pública: " + (Get-Content $urlFile -First 1)) -ForegroundColor Green }
        $summary += "Túnel: OK"
    } else {
        Write-Host "No existe la tarea '$taskTunnel'. Créala una vez con: scripts\publish.ps1 -Hostname tu.dominio.com -Install" -ForegroundColor Yellow
        $summary += "Túnel: no instalado"
    }
} else {
    Write-Host "== 3/4 Túnel: omitido (añade -Tunnel si publicas la consola con publish.ps1 -Install)"
}

# ---- 4/4 doctor + consola ----
Write-Host "== 4/4 Revisión" -ForegroundColor Cyan
$py = Join-Path $root "engine\.venv\Scripts\python.exe"
if (Test-Path $py) {
    Push-Location (Join-Path $root "engine")
    try { & $py -m tradepilot.doctor } catch { Write-Host "El doctor no pudo ejecutarse: $_" -ForegroundColor Yellow }
    Pop-Location
}
Write-Host ""
Write-Host "Resumen:" -ForegroundColor Cyan
$summary | ForEach-Object { Write-Host "  - $_" }
if (-not $NoBrowser -and (Test-Port "127.0.0.1" $port)) {
    Start-Process "http://localhost:$port" | Out-Null
    Write-Host "Consola abierta: http://localhost:$port (entra con el API_TOKEN de engine\.env)." -ForegroundColor Green
}
if ($summary -match "NO responde") { exit 1 }
exit 0
