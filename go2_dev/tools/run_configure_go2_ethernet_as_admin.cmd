@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%configure_go2_ethernet_admin.ps1"

echo This will request Administrator permission to configure Ethernet for Go2.
echo Target IPv4: 192.168.123.222/24
echo Gateway: none
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%PS_SCRIPT%""'"

echo.
echo After the Administrator window finishes, run:
echo powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%check_go2_network.ps1"
endlocal
