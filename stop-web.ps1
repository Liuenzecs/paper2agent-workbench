param([string]$DataDir = "")
$ErrorActionPreference = "Stop"
if (-not $DataDir) {
    if ($env:PAPER2AGENT_DATA_DIR) { $DataDir = $env:PAPER2AGENT_DATA_DIR }
    else { $DataDir = Join-Path $env:LOCALAPPDATA "Paper2AgentLab" }
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
$trialPidPath = Join-Path $DataDir "web.pid"
if (-not (Test-Path -LiteralPath $trialPidPath)) { Write-Host "No background service recorded."; exit 0 }
$trialRecordedPid = [int](Get-Content -LiteralPath $trialPidPath)
$trialProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$trialRecordedPid"
$trialScript = Join-Path $PSScriptRoot "src\lab_cli.py"
if ($trialProcess -and $trialProcess.CommandLine -and $trialProcess.CommandLine.Contains($trialScript) -and $trialProcess.CommandLine.Contains($DataDir)) {
    Stop-Process -Id $trialRecordedPid
    Write-Host "Stopped the recorded Paper2Agent web service. Saved results remain in the data directory."
} elseif ($trialProcess) { throw "The recorded process ID now belongs to another process; it was not stopped." }
