#requires -Version 7.0

param(
    [switch]$PrepareOnly,
    [string]$LauncherRepositoryUrl = 'git@github.com:ok-oldking/pyappify.git'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $ProjectRoot 'build/pyappify'
$OutputRoot = Join-Path $ProjectRoot 'dist/ok-ww-personal'
$LauncherName = 'ok-ww-personal'
$LauncherVersion = 'v1.2.3'
$LauncherCommit = '52bc6d986aae93615673960256549f51f13c92b6'
$PatchPath = Join-Path $ProjectRoot 'launcher/pyappify-profiles.patch'
$ConfigPath = Join-Path $ProjectRoot 'launcher/pyappify.personal.yml'
$PortableExePath = Join-Path $OutputRoot "$LauncherName.exe"
$InstallerPath = Join-Path $ProjectRoot "dist/$LauncherName-win32-setup.exe"
$ArchivePath = Join-Path $ProjectRoot "dist/$LauncherName-win32.zip"
$Utf8 = [System.Text.UTF8Encoding]::new($false)

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot '.git'))) {
    if (Test-Path -LiteralPath $SourceRoot) {
        # Only the pinned checkout may live here; anything else (e.g. a stale
        # cache directory) is removed before the pinned clone below.
        $ResolvedSource = [System.IO.Path]::GetFullPath($SourceRoot)
        $ExpectedSource = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build/pyappify'))
        if ($ResolvedSource -ne $ExpectedSource) {
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
    if ($LASTEXITCODE -ne 0) {
        throw 'The launcher patch cannot be applied to this source checkout.'
    }
}

Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $SourceRoot 'src-tauri/assets/pyappify.yml')
foreach ($Icon in @('icon.ico', 'icon.png')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "icons/$Icon") `
        -Destination (Join-Path $SourceRoot "src-tauri/icons/$Icon")
}

$TauriPath = Join-Path $SourceRoot 'src-tauri/tauri.conf.json'
$TauriConfig = Get-Content -LiteralPath $TauriPath -Raw | ConvertFrom-Json
$TauriConfig.productName = $LauncherName
$TauriConfig.mainBinaryName = $LauncherName
$TauriConfig.identifier = 'com.nancyingcode.ok-ww-personal'
$TauriConfig.version = $LauncherVersion.TrimStart('v')
$TauriConfig.app.windows[0].title = 'ok-ww - Official / Personal'
# The installer stays an online setup: the chosen source (Official / Personal)
# is downloaded on first setup instead of being baked into the package.
$TauriConfig.bundle.resources = @()
[System.IO.File]::WriteAllText($TauriPath, ($TauriConfig | ConvertTo-Json -Depth 20) + "`n", $Utf8)

$CargoPath = Join-Path $SourceRoot 'src-tauri/Cargo.toml'
$CargoConfig = [System.IO.File]::ReadAllText($CargoPath).Replace('name = "pyappify"', 'name = "ok-ww-personal"')
[System.IO.File]::WriteAllText($CargoPath, $CargoConfig, $Utf8)
$BuildPath = Join-Path $SourceRoot 'src-tauri/build.rs'
$BuildConfig = [System.IO.File]::ReadAllText($BuildPath).Replace('const UAC: bool = false;', 'const UAC: bool = true;')
[System.IO.File]::WriteAllText($BuildPath, $BuildConfig, $Utf8)

if ($PrepareOnly) {
    Write-Host "Launcher source and Official / Personal configuration prepared in $SourceRoot"
    return
}

$PnpmCommand = (Get-Command pnpm.cmd -ErrorAction Stop).Source
$CargoCommand = (Get-Command cargo.exe -ErrorAction Stop).Source

if (Test-Path -LiteralPath (Join-Path $SourceRoot 'node_modules')) {
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

Push-Location $SourceRoot
try {
    Invoke-Checked $PnpmCommand @('install', '--frozen-lockfile')
    Invoke-Checked $PnpmCommand @('build')
    Invoke-Checked $CargoCommand @('test', '--release', '--manifest-path', 'src-tauri/Cargo.toml', '--lib', 'launcher_profiles')
    Invoke-Checked $PnpmCommand @('tauri', 'build', '--no-bundle')
    # Bundling is a separate step so the portable executable above is reused
    # instead of being rebuilt from scratch by the Tauri bundler.
    Invoke-Checked $PnpmCommand @('tauri', 'bundle')
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot "src-tauri/target/release/$LauncherName.exe") -Destination $OutputRoot
Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $OutputRoot 'pyappify.yml')
$OutputIcons = Join-Path $OutputRoot 'icons'
New-Item -ItemType Directory -Path $OutputIcons -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'icons/icon.png') -Destination $OutputIcons

$BundleRoot = Join-Path $SourceRoot 'src-tauri/target/release/bundle/nsis'
$InstallerSource = Get-ChildItem -LiteralPath $BundleRoot -File -Filter '*.exe' -ErrorAction SilentlyContinue |
    Sort-Object -Property LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (-not $InstallerSource) {
    throw "The NSIS installer was not produced in $BundleRoot."
}
Copy-Item -LiteralPath $InstallerSource.FullName -Destination $InstallerPath -Force

$DistributionFiles = @(
    $PortableExePath,
    (Join-Path $OutputRoot 'pyappify.yml'),
    $OutputIcons
)
Compress-Archive -LiteralPath $DistributionFiles -DestinationPath $ArchivePath -Force

Write-Host "Portable launcher: $PortableExePath"
Write-Host "Portable archive: $ArchivePath"
Write-Host "Installer (Official / Personal): $InstallerPath"