# Publica la consola de TradePilot X en Internet con Cloudflare Tunnel (sin abrir puertos en el router, con HTTPS).
# El engine sigue en este PC (tiene que estar junto a NinjaTrader); el túnel solo le da una puerta de entrada segura.
#
#   scripts\publish.ps1                                   túnel RÁPIDO: URL aleatoria https://xxxx.trycloudflare.com
#                                                         (sin cuenta; la URL cambia cada vez que se arranca)
#   scripts\publish.ps1 -Hostname consola.midominio.com   túnel con NOMBRE FIJO (tu dominio en Cloudflare; login una vez)
#   scripts\publish.ps1 -Hostname ... -Install            además lo registra para que arranque solo al iniciar sesión
#   scripts\publish.ps1 -Uninstall                        quita la tarea programada y la URL pública
#
# Requisitos: engine arrancado (scripts\run_engine.ps1 o la tarea "TradePilotX Engine") y API_TOKEN fuerte en engine\.env
# (este script lo genera si es el de ejemplo).
param([string]$Hostname = "", [switch]$Install, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root "tools"
$cf = Join-Path $tools "cloudflared.exe"
$envFile = Join-Path $root "engine\.env"
$urlFile = Join-Path $root "engine\data\public_url.txt"
$taskName = "TradePilotX Tunnel"
$tunnelName = "tradepilotx"
New-Item -ItemType Directory -Force -Path $tools, (Join-Path $root "engine\data") | Out-Null

function Read-Env([string]$key, [string]$default) {
    if (Test-Path $envFile) { $m = Select-String -Path $envFile -Pattern "^\s*$key=(.*)$"; if ($m) { return $m.Matches[0].Groups[1].Value.Trim() } }
    return $default
}
function Restart-Engine {
    $t = Get-ScheduledTask -TaskName "TradePilotX Engine" -ErrorAction SilentlyContinue
    if ($t) { Stop-ScheduledTask -TaskName "TradePilotX Engine" -ErrorAction SilentlyContinue; Start-Sleep 2; Start-ScheduledTask -TaskName "TradePilotX Engine"; Write-Host "Engine reiniciado (tarea programada)." -ForegroundColor Green }
    else { Write-Host "Reinicia el engine a mano para que use el token nuevo (cierra su ventana y ejecuta scripts\run_engine.ps1)." -ForegroundColor Yellow }
}
function Show-Url([string]$url) {
    Write-Host ""; Write-Host "  CONSOLA PUBLICADA EN:  $url" -ForegroundColor Green; Write-Host ""
    Write-Host "  Abre esa dirección en el teléfono, entra con el token de engine\.env (API_TOKEN) e instálala:"
    Write-Host "  Android: Chrome > menú > Instalar aplicación   |   iPhone: Safari > Compartir > Añadir a pantalla de inicio"
    Write-Host "  (en la consola, abajo a la izquierda, 'Acceso remoto' muestra esta URL con un código QR para escanear)"
    $py = Join-Path $root "engine\.venv\Scripts\python.exe"
    if (Test-Path $py) { try { & $py -c "import qrcode; q = qrcode.QRCode(border=1); q.add_data('$url'); q.print_ascii(invert=True)" } catch { } }
    Write-Host ""
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Item $urlFile -ErrorAction SilentlyContinue
    Write-Host "Túnel detenido y URL pública borrada. La consola solo es accesible en local." -ForegroundColor Green
    exit 0
}

# 1) Token fuerte obligatorio antes de exponer la consola
$token = Read-Env "API_TOKEN" "cambiame"
if ($token.Length -lt 16 -or $token -in @("cambiame", "changeme", "password", "token")) {
    $new = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root "engine\.env.example") $envFile }
    $content = Get-Content $envFile -Raw
    if ($content -match "(?m)^\s*API_TOKEN=") { $content = $content -replace "(?m)^\s*API_TOKEN=.*$", "API_TOKEN=$new" } else { $content += "`nAPI_TOKEN=$new`n" }
    Set-Content -Path $envFile -Value $content -NoNewline
    Write-Host "El API_TOKEN era débil: se ha generado uno nuevo en engine\.env:" -ForegroundColor Yellow
    Write-Host "    $new" -ForegroundColor Cyan
    Write-Host "Guárdalo (es la contraseña de la consola). Las sesiones abiertas tendrán que volver a entrar." -ForegroundColor Yellow
    Restart-Engine
}
$port = [int](Read-Env "API_PORT" "8000")

# 2) cloudflared
if (-not (Test-Path $cf)) {
    Write-Host "Descargando cloudflared..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf -UseBasicParsing
}

if (-not $Hostname) {
    # 3a) Túnel rápido: URL aleatoria, sin cuenta. Se queda en primer plano; al cerrar la ventana se corta.
    $log = Join-Path $tools "tunnel.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    $proc = Start-Process -FilePath $cf -ArgumentList "tunnel --url http://localhost:$port --no-autoupdate" -RedirectStandardError $log -PassThru -WindowStyle Hidden
    Write-Host "Abriendo túnel rápido hacia http://localhost:$port ..." -ForegroundColor Cyan
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep 1
        if (Test-Path $log) { $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1; if ($m) { $url = $m.Matches[0].Value } }
    }
    if (-not $url) { Write-Host "No se obtuvo la URL del túnel. Revisa $log" -ForegroundColor Red; Stop-Process $proc -Force; exit 1 }
    Set-Content -Path $urlFile -Value $url
    Show-Url $url
    Write-Host "Esta URL cambia cada vez que arrancas el túnel. Para una fija, usa -Hostname con tu dominio en Cloudflare." -ForegroundColor Yellow
    Write-Host "Deja esta ventana abierta (Ctrl+C para cerrar el túnel)."
    try { Wait-Process -Id $proc.Id } finally { Remove-Item $urlFile -ErrorAction SilentlyContinue }
    exit 0
}

# 3b) Túnel con nombre: dominio propio en Cloudflare (plan gratuito), URL fija y HTTPS
$cert = Join-Path $env:USERPROFILE ".cloudflared\cert.pem"
if (-not (Test-Path $cert)) {
    Write-Host "Se abrirá el navegador para iniciar sesión en Cloudflare y elegir el dominio (una sola vez)." -ForegroundColor Cyan
    & $cf tunnel login
}
$list = & $cf tunnel list -o json 2>$null | ConvertFrom-Json
$existing = $list | Where-Object { $_.name -eq $tunnelName }
if (-not $existing) { & $cf tunnel create $tunnelName | Out-Null; $list = & $cf tunnel list -o json | ConvertFrom-Json; $existing = $list | Where-Object { $_.name -eq $tunnelName } }
$id = $existing.id
$cred = Join-Path $env:USERPROFILE ".cloudflared\$id.json"
$config = Join-Path $tools "tunnel.yml"
@"
tunnel: $id
credentials-file: $cred
ingress:
  - hostname: $Hostname
    service: http://localhost:$port
  - service: http_status:404
"@ | Set-Content -Path $config
& $cf tunnel route dns --overwrite-dns $tunnelName $Hostname | Out-Null
Set-Content -Path $urlFile -Value "https://$Hostname"
Show-Url "https://$Hostname"
if ($Install) {
    $action = New-ScheduledTaskAction -Execute $cf -Argument "--config `"$config`" tunnel run $tunnelName" -WorkingDirectory $tools
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0) -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Tarea '$taskName' registrada: el túnel arranca solo al iniciar sesión y se reconecta si se cae." -ForegroundColor Green
    Write-Host "Quitar:  scripts\publish.ps1 -Uninstall"
} else {
    Write-Host "Túnel en primer plano (Ctrl+C para cerrar). Para dejarlo fijo: scripts\publish.ps1 -Hostname $Hostname -Install" -ForegroundColor Yellow
    & $cf --config $config tunnel run $tunnelName
}
