param([int]$Port = 8765, [string]$DataDir = "")
$ErrorActionPreference = "Stop"
$workbenchRoot = $PSScriptRoot
$workbenchPython = Join-Path $workbenchRoot ".venv\Scripts\python.exe"
$runtimePathFile = Join-Path $workbenchRoot 'runtime-path.txt'
if (Test-Path -LiteralPath $runtimePathFile) { $workbenchPython = (Get-Content -LiteralPath $runtimePathFile -Raw -Encoding UTF8).Trim() }
if (-not (Test-Path -LiteralPath $workbenchPython)) { throw "Run install.ps1 first." }
if (-not $DataDir) {
    if ($env:PAPER2AGENT_DATA_DIR) { $DataDir = $env:PAPER2AGENT_DATA_DIR }
    else { $DataDir = Join-Path $env:LOCALAPPDATA "Paper2AgentLab" }
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
$workbenchAddress = "http://127.0.0.1:$Port"
try {
    $workbenchExisting = Invoke-RestMethod -Uri "$workbenchAddress/api/status" -TimeoutSec 2
    if ($workbenchExisting.data_dir -and ([IO.Path]::GetFullPath($workbenchExisting.data_dir) -eq [IO.Path]::GetFullPath($DataDir))) {
        Start-Process $workbenchAddress
        exit 0
    }
    throw "This port belongs to another workspace. Choose another -Port."
} catch {
    if ($_.Exception.Message -like "This port belongs*") { throw }
}
$workbenchScript = Join-Path $workbenchRoot "src\lab_cli.py"
$workbenchArguments = @('-u', ('"' + $workbenchScript + '"'), '--data-dir', ('"' + $DataDir + '"'), 'web', '--port', $Port)
$workbenchProcess = Start-Process -FilePath $workbenchPython -ArgumentList $workbenchArguments -WorkingDirectory $workbenchRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataDir "web.stdout.log") -RedirectStandardError (Join-Path $DataDir "web.stderr.log")
$workbenchProcess.Id | Set-Content -LiteralPath (Join-Path $DataDir "web.pid") -Encoding Ascii
$workbenchReady = $false
for ($workbenchAttempt = 0; $workbenchAttempt -lt 50; $workbenchAttempt++) {
    if ($workbenchProcess.HasExited) { throw "Web service failed to start. See web.stderr.log in the data directory." }
    try { Invoke-RestMethod -Uri "$workbenchAddress/api/status" -TimeoutSec 1 | Out-Null; $workbenchReady = $true; break } catch { Start-Sleep -Milliseconds 200 }
}
if (-not $workbenchReady) { throw "Web service did not become ready. See logs in the data directory." }
Write-Host ("Starting local web service, process " + $workbenchProcess.Id + ". Address: http://127.0.0.1:" + $Port)
Start-Process $workbenchAddress
