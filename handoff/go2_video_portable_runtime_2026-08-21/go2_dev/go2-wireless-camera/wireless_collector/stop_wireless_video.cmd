@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue; if($p){Stop-Process -Id $p.OwningProcess -Force; Write-Host 'Go2 wireless video stopped.'}else{Write-Host 'Go2 wireless video is not running.'}"
pause
