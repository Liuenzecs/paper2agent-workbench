param([string]$Python = "")
$ErrorActionPreference = "Stop"
$trialRoot = $PSScriptRoot
$trialEnvironment = Join-Path $trialRoot ".venv"
if (-not (Test-Path -LiteralPath (Join-Path $trialEnvironment "Scripts\python.exe"))) {
    if ($Python) {
        & $Python -m venv $trialEnvironment
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $trialEnvironment
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $trialEnvironment
    } else {
        throw "Install Python 3.11 first, then run install.ps1 again."
    }
    if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed." }
}
$trialPython = Join-Path $trialEnvironment "Scripts\python.exe"
& $trialPython -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "This trial package requires Python 3.11. Specify -Python with a Python 3.11 executable." }
& $trialPython -m pip install --use-feature=truststore -r (Join-Path $trialRoot "requirements-lock.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $trialPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency validation failed." }
Write-Host "Installed. Run Start-Web.cmd or .\paper.cmd status."
