param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CliArguments)
$ErrorActionPreference = 'Stop'
$cliPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$runtimePathFile = Join-Path $PSScriptRoot 'runtime-path.txt'
if (Test-Path -LiteralPath $runtimePathFile) { $cliPython = (Get-Content -LiteralPath $runtimePathFile -Raw -Encoding UTF8).Trim() }
if (-not (Test-Path -LiteralPath $cliPython)) { throw 'Run install.ps1 first.' }
& $cliPython (Join-Path $PSScriptRoot 'src\lab_cli.py') @CliArguments
exit $LASTEXITCODE
