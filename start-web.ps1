param([int]$Port = 8765, [string]$DataDir = "")
$ErrorActionPreference = "Stop"
$trialRoot = $PSScriptRoot
$trialPython = Join-Path $trialRoot ".venv\Scripts\python.exe"
$runtimePathFile = Join-Path $trialRoot 'runtime-path.txt'
if (Test-Path -LiteralPath $runtimePathFile) { $trialPython = (Get-Content -LiteralPath $runtimePathFile -Raw -Encoding UTF8).Trim() }
if (-not (Test-Path -LiteralPath $trialPython)) { throw "Run install.ps1 first." }
if (-not $DataDir) {
    if ($env:PAPER2AGENT_DATA_DIR) { $DataDir = $env:PAPER2AGENT_DATA_DIR }
    else { $DataDir = Join-Path $env:LOCALAPPDATA "Paper2AgentLab" }
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
$trialAddress = "http://127.0.0.1:$Port"
try {
    $trialExisting = Invoke-RestMethod -Uri "$trialAddress/api/status" -TimeoutSec 2
    if ($trialExisting.data_dir -and ([IO.Path]::GetFullPath($trialExisting.data_dir) -eq [IO.Path]::GetFullPath($DataDir))) {
        Start-Process $trialAddress
        exit 0
    }
    throw "This port belongs to another workspace. Choose another -Port."
} catch {
    if ($_.Exception.Message -like "This port belongs*") { throw }
}
$trialScript = Join-Path $trialRoot "src\lab_cli.py"
$trialArguments = @('-u', ('"' + $trialScript + '"'), '--data-dir', ('"' + $DataDir + '"'), 'web', '--port', $Port)
$trialProcess = Start-Process -FilePath $trialPython -ArgumentList $trialArguments -WorkingDirectory $trialRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataDir "web.stdout.log") -RedirectStandardError (Join-Path $DataDir "web.stderr.log")
$trialProcess.Id | Set-Content -LiteralPath (Join-Path $DataDir "web.pid") -Encoding Ascii
$trialReady = $false
for ($trialAttempt = 0; $trialAttempt -lt 50; $trialAttempt++) {
    if ($trialProcess.HasExited) { throw "Web service failed to start. See web.stderr.log in the data directory." }
    try { Invoke-RestMethod -Uri "$trialAddress/api/status" -TimeoutSec 1 | Out-Null; $trialReady = $true; break } catch { Start-Sleep -Milliseconds 200 }
}
if (-not $trialReady) { throw "Web service did not become ready. See logs in the data directory." }
Write-Host ("Starting local web service, process " + $trialProcess.Id + ". Address: http://127.0.0.1:" + $Port)
Start-Process $trialAddress
