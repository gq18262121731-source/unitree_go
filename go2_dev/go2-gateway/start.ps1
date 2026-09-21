param(
    [string]$RobotIp = "192.168.8.254"
)

& "$PSScriptRoot\scripts\Start-Go2WirelessRuntime.ps1" `
    -RobotIp $RobotIp `
    -PythonPath "C:\Users\13010\anaconda3\envs\torchgpu\python.exe" `
    -NoOpenBrowser

exit $LASTEXITCODE
