#requires -Version 7.0

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

function Write-BytesIfChanged {
    param([string]$Path, [byte[]]$Bytes)

    if (Test-Path -LiteralPath $Path) {
        $Existing = [System.IO.File]::ReadAllBytes($Path)
        if ([System.Collections.StructuralComparisons]::StructuralEqualityComparer.Equals($Existing, $Bytes)) { return }
    }
    [System.IO.File]::WriteAllBytes($Path, $Bytes)
}

function Write-TextIfChanged {
    param([string]$Path, [string]$Content)

    Write-BytesIfChanged $Path ([System.Text.UTF8Encoding]::new($false).GetBytes($Content))
}

function Copy-IfChanged {
    param([string]$Source, [string]$Destination)

    Write-BytesIfChanged $Destination ([System.IO.File]::ReadAllBytes($Source))
}

function Invoke-Timed {
    param([string]$Name, [scriptblock]$Action)

    $StartedAt = [DateTime]::UtcNow.ToString('o')
    $Timer = [System.Diagnostics.Stopwatch]::StartNew()
    $Succeeded = $false
    try {
        & $Action
        $Succeeded = $true
    } finally {
        $Timer.Stop()
        $Record = [ordered]@{
            stage = $Name
            startedAt = $StartedAt
            seconds = [Math]::Round($Timer.Elapsed.TotalSeconds, 3)
            succeeded = $Succeeded
        }
        try {
            $Timings = @()
            if (Test-Path -LiteralPath $TimingPath) {
                $Timings = @(Get-Content -LiteralPath $TimingPath -Raw | ConvertFrom-Json)
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $TimingPath) -Force | Out-Null
            Write-TextIfChanged $TimingPath (ConvertTo-Json -InputObject @($Timings + $Record) -Depth 10)
        } catch {
            # Reporting must not replace the build command's success/failure.
            Write-Warning "Cannot write launcher timing record: $_"
        }
        Write-Host "$Name`: $($Record.seconds)s (success=$Succeeded)"
    }
}
