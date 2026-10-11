param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$exampleRoot = Join-Path $PSScriptRoot 'mcp'
$exampleEnv = Join-Path $exampleRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $exampleEnv 'Scripts\python.exe'))) {
    & $Python -m venv $exampleEnv
    if ($LASTEXITCODE -ne 0) { throw 'Vendi environment creation failed.' }
}
$examplePython = Join-Path $exampleEnv 'Scripts\python.exe'
& $examplePython -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Use Python 3.11 for the Vendi example.' }
& $examplePython -m pip install --use-feature=truststore -r (Join-Path $exampleRoot 'src\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Vendi dependency installation failed.' }
& $examplePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Vendi dependency validation failed.' }
Write-Host ('Vendi interpreter: ' + $examplePython)
