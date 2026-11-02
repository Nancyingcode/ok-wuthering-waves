param(
    [int]$ShardIndex = 0,
    [int]$ShardCount = 1
)

$ErrorActionPreference = 'Stop'
$Python = if (Test-Path (Join-Path $PSScriptRoot '.venv/Scripts/python.exe')) {
  Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
} elseif (Test-Path (Join-Path $PSScriptRoot '.venv/bin/python')) {
  Join-Path $PSScriptRoot '.venv/bin/python'
} else {
  "python"
}

& $Python -u (Join-Path $PSScriptRoot 'scripts/run_python_tests.py') --shard-index $ShardIndex --shard-count $ShardCount
exit $LASTEXITCODE
