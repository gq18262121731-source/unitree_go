$port = 9200
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCandidates = @(
  (Join-Path $env:USERPROFILE "anaconda3\envs\health\python.exe"),
  (Join-Path $env:USERPROFILE ".conda\envs\health\python.exe"),
  (Join-Path $env:USERPROFILE "anaconda3\envs\AI\python.exe"),
  (Join-Path $env:USERPROFILE ".conda\envs\AI\python.exe")
) | Where-Object { $_ }
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}

$existing = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" } | Select-Object -First 1
if ($existing) {
  Write-Host "target-user-console is already listening on http://127.0.0.1:$port/"
  Start-Process "http://127.0.0.1:$port/"
  exit 0
}

if (-not $python -or -not (Test-Path -LiteralPath $python)) {
  throw "Python not found: $python"
}

$out = Join-Path $root "target-user-console.out.log"
$err = Join-Path $root "target-user-console.err.log"

$proc = Start-Process `
  -FilePath $python `
  -ArgumentList @("-m", "http.server", "$port", "--bind", "127.0.0.1") `
  -WorkingDirectory $root `
  -RedirectStandardOutput $out `
  -RedirectStandardError $err `
  -WindowStyle Hidden `
  -PassThru

Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:$port/"
Write-Host "target-user-console started at http://127.0.0.1:$port/ (PID: $($proc.Id))"
