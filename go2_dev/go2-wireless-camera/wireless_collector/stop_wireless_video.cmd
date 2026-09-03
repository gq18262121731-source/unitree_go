@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue; if(-not $p){Write-Host 'Go2 wireless video/runtime is not running.'; exit 0}; try{$s=Invoke-RestMethod 'http://127.0.0.1:8093/status' -TimeoutSec 2}catch{$s=$null}; if($s.runtimeId -eq 'go2-wireless-runtime'){Write-Host 'Unified Runtime is active. Type EXIT in its console so StopMove and WebRTC cleanup can complete safely.'; exit 2}; Stop-Process -Id $p.OwningProcess -Force; Write-Host 'Legacy Go2 wireless video stopped.'"
pause
