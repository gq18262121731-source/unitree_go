@echo off
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0start_sta_wireless.ps1" -RobotIp 192.168.8.252 -ListenHost 0.0.0.0 -NoOpenBrowser -Foreground
