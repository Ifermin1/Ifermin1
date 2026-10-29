@echo off
rem TradePilot X: doble clic y arranca todo (NinjaTrader + addon, engine, consola).
rem Acepta las mismas opciones que start_all.ps1:  start_all.bat -Tunnel -NoBrowser -NinjaPath "C:\...\NinjaTrader.exe"
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
set RC=%ERRORLEVEL%
echo.
if %RC% NEQ 0 echo Algo no respondio (codigo %RC%). Lee los mensajes en rojo de arriba.
echo %cmdcmdline% | find /i "/c" >nul && pause
exit /b %RC%
