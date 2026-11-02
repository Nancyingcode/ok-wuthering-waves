#requires -Version 7.0

param(
    [ValidateSet('All', 'Prepare', 'Build', 'Test', 'Bundle')]
    [string]$Phase = 'All',
    [switch]$PrepareOnly,
    [string]$LauncherRepositoryUrl = 'git@github.com:ok-oldking/pyappify.git'
)

$ErrorActionPreference = 'Stop'
if ($PrepareOnly) {
    $HasConflictingPhase = $PSBoundParameters.ContainsKey('Phase') -and $Phase -ne 'Prepare'
    if ($HasConflictingPhase) {
        throw '-PrepareOnly cannot be combined with a phase other than Prepare.'
    }
    $Phase = 'Prepare'
}
. (Join-Path $PSScriptRoot 'personal_launcher_common.ps1')
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $ProjectRoot 'build/pyappify'
$CacheTool = Join-Path $PSScriptRoot 'personal_launcher_cache.cjs'
$TimingPath = Join-Path $ProjectRoot 'build/launcher-metadata/timings.json'
$BuildConfigPath = Join-Path $ProjectRoot 'build/launcher-metadata/tauri-build.json'
$SettingsJson = & node $CacheTool settings
if ($LASTEXITCODE -ne 0) { throw 'Cannot load launcher settings.' }
$Settings = $SettingsJson | ConvertFrom-Json
$LauncherName = $Settings.name
$LauncherVersion = $Settings.version
$LauncherCommit = $Settings.commit
$OutputRoot = Join-Path $ProjectRoot "dist/$LauncherName"
$PatchPath = Join-Path $ProjectRoot 'launcher/pyappify-profiles.patch'
$ConfigPath = Join-Path $ProjectRoot 'launcher/pyappify.personal.yml'
$InstallerPath = Join-Path $ProjectRoot "dist/$LauncherName-win32-setup.exe"
$ArchivePath = Join-Path $ProjectRoot "dist/$LauncherName-win32.zip"

function Invoke-CacheTool {
    param([string]$Command, [string]$Argument)
    Invoke-Checked 'node' @($CacheTool, $Command, $ProjectRoot, $Argument)
}

function Prepare-Launcher {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot '.git'))) {
        if (Test-Path -LiteralPath $SourceRoot) {
            # Prepare precedes dependency-cache restore. Never clear target in
            # later phases, where it may contain restored Rust dependencies.
            $ResolvedSource = [System.IO.Path]::GetFullPath($SourceRoot)
            $ExpectedSource = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build/pyappify'))
            $IsLinkedSource = (Get-Item -LiteralPath $SourceRoot -Force).LinkType
            $IsLinkedBuild = (Get-Item -LiteralPath (Split-Path -Parent $SourceRoot) -Force).LinkType
            $CanRemoveSource = $ResolvedSource -eq $ExpectedSource -and -not $IsLinkedSource -and -not $IsLinkedBuild
            if (-not $CanRemoveSource) {
                throw "Refusing to remove unexpected launcher source path: $ResolvedSource"
            }
            Remove-Item -LiteralPath $ResolvedSource -Recurse -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $SourceRoot) -Force | Out-Null
        Invoke-Checked 'git' @('clone', '--depth', '1', '--branch', $LauncherVersion,
            $LauncherRepositoryUrl, $SourceRoot)
    }

    $ActualCommit = & git -C $SourceRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $ActualCommit.Trim() -ne $LauncherCommit) {
        throw "Expected PyAppify $LauncherVersion ($LauncherCommit) in $SourceRoot."
    }
    & git -C $SourceRoot apply --check --whitespace=error-all $PatchPath 2>$null
    if ($LASTEXITCODE -eq 0) {
        Invoke-Checked 'git' @('-C', $SourceRoot, 'apply', '--whitespace=error-all', $PatchPath)
    } else {
        & git -C $SourceRoot apply --reverse --check $PatchPath 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'The launcher patch cannot be applied to this source checkout.' }
    }

    Copy-IfChanged $ConfigPath (Join-Path $SourceRoot 'src-tauri/assets/pyappify.yml')
    foreach ($Icon in @('icon.ico', 'icon.png')) {
        Copy-IfChanged (Join-Path $ProjectRoot "icons/$Icon") (Join-Path $SourceRoot "src-tauri/icons/$Icon")
    }
    $TauriPath = Join-Path $SourceRoot 'src-tauri/tauri.conf.json'
    $TauriConfig = Get-Content -LiteralPath $TauriPath -Raw | ConvertFrom-Json
    $TauriConfig.productName = $LauncherName
    $TauriConfig.mainBinaryName = $LauncherName
    $TauriConfig.identifier = 'com.nancyingcode.ok-ww-personal'
    $TauriConfig.version = $LauncherVersion.TrimStart('v')
    $TauriConfig.app.windows[0].title = 'ok-ww - Official / Personal'
    # Keep the installer online: business code is fetched from the selected
    # profile, so Python-only commits need not invalidate launcher artifacts.
    $TauriConfig.bundle.resources = @()
    Write-TextIfChanged $TauriPath (($TauriConfig | ConvertTo-Json -Depth 20) + "`n")

    $CargoPath = Join-Path $SourceRoot 'src-tauri/Cargo.toml'
    # Tauri rewrites the manifest with LF even on CRLF checkouts. Normalize
    # before snapshotting; retain this while the CLI rewrites Cargo.toml.
    $CargoConfig = [System.IO.File]::ReadAllText($CargoPath).Replace('name = "pyappify"', 'name = "ok-ww-personal"').Replace("`r`n", "`n")
    Write-TextIfChanged $CargoPath $CargoConfig
    $BuildPath = Join-Path $SourceRoot 'src-tauri/build.rs'
    Write-TextIfChanged $BuildPath ([System.IO.File]::ReadAllText($BuildPath).Replace('const UAC: bool = false;', 'const UAC: bool = true;'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $BuildConfigPath) -Force | Out-Null
    Write-TextIfChanged $BuildConfigPath ('{"build":{"beforeBuildCommand":""}}' + "`n")
    Invoke-CacheTool 'prepare-lock'
    Invoke-CacheTool 'prepare'
    Write-Host "Launcher source prepared in $SourceRoot"
}

function Stop-LauncherDependencyProcesses {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot 'node_modules'))) { return }
    $NormalizedSource = $SourceRoot.Replace('\', '/')
    $DependencyProcesses = Get-CimInstance Win32_Process -Filter `
        "Name = 'node.exe' OR Name = 'electron.exe' OR Name = 'esbuild.exe'" | Where-Object {
        $ExecutableBelongsToLauncher = $_.ExecutablePath -and $_.ExecutablePath.Replace('\', '/').StartsWith("$NormalizedSource/", [StringComparison]::OrdinalIgnoreCase)
        $CommandBelongsToLauncher = $_.CommandLine -and $_.CommandLine.Replace('\', '/').Contains("$NormalizedSource/", [StringComparison]::OrdinalIgnoreCase)
        $ExecutableBelongsToLauncher -or $CommandBelongsToLauncher
    }
    foreach ($DependencyProcess in $DependencyProcesses) {
        Write-Host "Stopping launcher dependency process $($DependencyProcess.ProcessId)."
        Stop-Process -Id $DependencyProcess.ProcessId -ErrorAction Stop
    }
}

if ($Phase -in @('All', 'Prepare')) {
    Invoke-Timed 'Prepare' { Prepare-Launcher }
}
if ($Phase -eq 'Prepare') { return }
Invoke-CacheTool 'assert-prepared'

$PnpmCommand = (Get-Command pnpm.cmd -ErrorAction Stop).Source
$CargoCommand = (Get-Command cargo.exe -ErrorAction Stop).Source
Push-Location $SourceRoot
try {
    if ($Phase -in @('All', 'Build')) {
        # Invalidate receipts before invoking tools, so a failed retry cannot
        # qualify old binaries for a fresh cache manifest or later bundle.
        foreach ($Receipt in @('Build', 'Test', 'Bundle')) {
            Remove-Item -LiteralPath (Join-Path $ProjectRoot "build/launcher-metadata/$Receipt.json") -Force -ErrorAction SilentlyContinue
        }
        Invoke-Timed 'pnpm install' {
            Stop-LauncherDependencyProcesses
            Invoke-Checked $PnpmCommand @('install', '--frozen-lockfile')
        }
        Invoke-Timed 'Frontend TypeScript and Vite build' {
            Invoke-Checked $PnpmCommand @('build')
        }
        # The explicit frontend build permits separate timing. The hashed
        # overlay disables only its duplicate Tauri hook, retaining Build -> Test.
        Invoke-Timed 'Tauri and Rust release build' {
            Invoke-Checked $PnpmCommand @('tauri', 'build', '--no-bundle', '--config', $BuildConfigPath)
        }
        Invoke-CacheTool 'receipt' 'Build'
    }
    if ($Phase -in @('All', 'Test')) {
        Invoke-CacheTool 'require' 'Build'
        foreach ($Receipt in @('Test', 'Bundle')) {
            Remove-Item -LiteralPath (Join-Path $ProjectRoot "build/launcher-metadata/$Receipt.json") -Force -ErrorAction SilentlyContinue
        }
        Invoke-Timed 'Compile and run launcher_profiles' {
            Invoke-Checked $CargoCommand @('test', '--release', '--locked', '--manifest-path', 'src-tauri/Cargo.toml', '--lib', 'launcher_profiles')
        }
        Invoke-CacheTool 'receipt' 'Test'
    }
    if ($Phase -in @('All', 'Bundle')) {
        Invoke-CacheTool 'require' 'Build'
        Invoke-CacheTool 'require' 'Test'
        Remove-Item -LiteralPath (Join-Path $ProjectRoot 'build/launcher-metadata/Bundle.json') -Force -ErrorAction SilentlyContinue
        Invoke-Timed 'NSIS bundle' { Invoke-Checked $PnpmCommand @('tauri', 'bundle', '--config', $BuildConfigPath) }
        Invoke-Timed 'Portable ZIP and collect outputs' {
            New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
            Copy-IfChanged (Join-Path $SourceRoot "src-tauri/target/release/$LauncherName.exe") (Join-Path $OutputRoot "$LauncherName.exe")
            Copy-IfChanged $ConfigPath (Join-Path $OutputRoot 'pyappify.yml')
            $OutputIcons = Join-Path $OutputRoot 'icons'
            New-Item -ItemType Directory -Path $OutputIcons -Force | Out-Null
            Copy-IfChanged (Join-Path $ProjectRoot 'icons/icon.png') (Join-Path $OutputIcons 'icon.png')
            $BundleRoot = Join-Path $SourceRoot 'src-tauri/target/release/bundle/nsis'
            $InstallerSource = Get-ChildItem -LiteralPath $BundleRoot -File -Filter '*.exe' -ErrorAction SilentlyContinue |
                Sort-Object -Property LastWriteTimeUtc -Descending | Select-Object -First 1
            if (-not $InstallerSource) { throw "The NSIS installer was not produced in $BundleRoot." }
            Copy-IfChanged $InstallerSource.FullName $InstallerPath
            $DistributionFiles = @((Join-Path $OutputRoot "$LauncherName.exe"), (Join-Path $OutputRoot 'pyappify.yml'), $OutputIcons)
            Compress-Archive -LiteralPath $DistributionFiles -DestinationPath $ArchivePath -Force
        }
        Invoke-CacheTool 'receipt' 'Bundle'
        Write-Host "Portable launcher: $OutputRoot/$LauncherName.exe"
        Write-Host "Portable archive: $ArchivePath"
        Write-Host "Installer (Official / Personal): $InstallerPath"
    }
} finally {
    Pop-Location
}
