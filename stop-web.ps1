param([string]$DataDir = "")
$ErrorActionPreference = "Stop"
if (-not $DataDir) {
    if ($env:PAPER2AGENT_DATA_DIR) { $DataDir = $env:PAPER2AGENT_DATA_DIR }
    else { $DataDir = Join-Path $env:LOCALAPPDATA "Paper2AgentLab" }
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
$workbenchPidPath = Join-Path $DataDir "web.pid"
if (-not (Test-Path -LiteralPath $workbenchPidPath)) { Write-Host "No background service recorded."; exit 0 }
$workbenchRecordedPid = [int](Get-Content -LiteralPath $workbenchPidPath)
$workbenchProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$workbenchRecordedPid"
$workbenchScript = Join-Path $PSScriptRoot "src\lab_cli.py"
if ($workbenchProcess -and $workbenchProcess.CommandLine -and $workbenchProcess.CommandLine.Contains($workbenchScript) -and $workbenchProcess.CommandLine.Contains($DataDir)) {
    Stop-Process -Id $workbenchRecordedPid
    Write-Host "Stopped the recorded Paper2Agent web service. Saved results remain in the data directory."
} elseif ($workbenchProcess) { throw "The recorded process ID now belongs to another process; it was not stopped." }
