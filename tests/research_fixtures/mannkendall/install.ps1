param([Parameter(Mandatory=$true)][string]$Python, [string]$EnvironmentDir = '')
$ErrorActionPreference = 'Stop'
$exampleRoot = $PSScriptRoot
$exampleEnv = if ($EnvironmentDir) { [IO.Path]::GetFullPath($EnvironmentDir) } else { Join-Path $exampleRoot '.venv' }
if (-not (Test-Path -LiteralPath (Join-Path $exampleEnv 'Scripts\python.exe'))) {
    & $Python -m venv $exampleEnv
    if ($LASTEXITCODE -ne 0) { throw 'Mann-Kendall environment creation failed.' }
}
$examplePython = Join-Path $exampleEnv 'Scripts\python.exe'
& $examplePython -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Use Python 3.11 for the Mann-Kendall example.' }
& $examplePython -m pip install --use-feature=truststore -r (Join-Path $exampleRoot 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Mann-Kendall dependency installation failed.' }
& $examplePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Mann-Kendall dependency validation failed.' }
Write-Host ('Mann-Kendall interpreter: ' + $examplePython)
