param(
    [ValidateSet('checks', 'compare', 'audit', 'idle', 'staging', 'managed', 'direct')]
    [string]$Stage = 'compare',
    [string]$Evidence,
    [string]$Round3 = 'logs/ocr-memory/round3-20261102-211933',
    [string]$Prior = 'logs/ocr-memory/followup-20261102-172923',
    [string]$Wheels = 'build/ocr-wheels/followup',
    [string]$Reference
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$python = Join-Path $repo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Repository .venv is required.' }
if (-not $Evidence) { $Evidence = "logs/ocr-memory/review-reproduce-$Stage-$(Get-Date -Format yyyyMMdd-HHmmss)" }
if (Test-Path -LiteralPath $Evidence) { throw "Use a new evidence directory: $Evidence" }
$Evidence = (New-Item -ItemType Directory -Path $Evidence).FullName
$env:ONEDNN_MAX_CPU_ISA = 'AVX2'
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = (New-Item -ItemType Directory -Path "$Evidence/tmp").FullName
$env:TMP = $env:TEMP
$commands = @()

function Invoke-ReviewPython([string]$Name, [string[]]$Argv) {
    $script:commands += ,@($python, $Argv)
    $script:commands | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$Evidence/commands.json" -Encoding utf8
    & $python @Argv 2>&1 | Tee-Object -FilePath "$Evidence/$Name.log"
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

switch ($Stage) {
    'checks' {
        foreach ($test in @('TestOcrIdleLocking', 'TestOcrMemory', 'TestOcrBackend',
                            'TestOcrRelease', 'TestOcrBenchmark', 'TestOcrObserver', 'TestOcrReplayCompare')) {
            Invoke-ReviewPython $test @('run_test.py', "tests/$test.py")
        }
    }
    'compare' {
        Invoke-ReviewPython 'strict-comparison' @('scripts/benchmark_ocr.py', 'compare', '--strict',
            '--reference', "$Round3/diagnostic-managed-retry.jsonl", "$Round3/diagnostic-direct.jsonl")
    }
    'audit' {
        Invoke-ReviewPython 'audit' @('scripts/audit_ocr_evidence.py', '--prior', $Prior,
            '--managed', "$Round3/diagnostic-managed-retry.jsonl", '--direct', "$Round3/diagnostic-direct.jsonl",
            '--output', "$Evidence/audit")
    }
    'idle' {
        Invoke-ReviewPython 'default-idle' @('scripts/benchmark_ocr_dependencies.py', '--case', 'compat',
            '--expect-ok', '2.0.22+okww.ocr1', '--expect-onnx', '0.0.22+okww.ocr1',
            '--output', "$Evidence/default-idle.jsonl")
    }
    'staging' {
        $app = (New-Item -ItemType Directory -Path "$Evidence/app").FullName
        foreach ($file in @('config.py', 'deploy.txt', 'requirements.txt', 'src')) {
            Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $app -Recurse
        }
        Invoke-ReviewPython 'staging' @('scripts/prepare_ocr_release.py', '--app', $app,
            '--wheels', $Wheels, '--tag', 'validation-ocr-review')
    }
    'managed' {
        Invoke-ReviewPython 'managed' @('scripts/benchmark_ocr_runtime.py', '--protocol', 'calls',
            '--calls', '8', '--period', '1', '--seconds', '15', '--cooldown', '31',
            '--manifest', 'scripts/ocr_real_inputs.json', '--diagnostics', '--output', "$Evidence/managed.jsonl")
    }
    'direct' {
        if (-not $Reference) { throw 'Pass --Reference as -Reference pointing to a completed managed short trace.' }
        Invoke-ReviewPython 'direct' @('scripts/diagnose_ocr_direct.py', '--reference', $Reference,
            '--output', "$Evidence/direct.jsonl")
    }
}
