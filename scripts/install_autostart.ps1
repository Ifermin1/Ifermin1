# Registra el engine de TradePilot X para que arranque solo al iniciar sesión en Windows
# y se reinicie si se cae. Ejecutar UNA vez como administrador:
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# Para quitarlo:  Unregister-ScheduledTask -TaskName "TradePilotX Engine" -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\run_engine.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "TradePilotX Engine" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Tarea 'TradePilotX Engine' registrada: arranca al iniciar sesión y se reinicia cada minuto si se cae." -ForegroundColor Green
Write-Host "Arrancarla ahora:  Start-ScheduledTask -TaskName 'TradePilotX Engine'"
Write-Host "Pararla:           Stop-ScheduledTask  -TaskName 'TradePilotX Engine'"
Write-Host "Ver la consola del engine: el proceso corre minimizado; los logs están en engine\logs\." -ForegroundColor Yellow
