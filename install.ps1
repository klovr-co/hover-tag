# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param(
    [string]$Version,
    [ValidateSet('stable', 'beta', 'alpha', 'edge')]
    [string]$Channel,
    [string]$BinDir,
    [switch]$SkipDependencies,
    [switch]$DependenciesOnly
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ is required. Install Python and enable its PATH option.'
}
if ($DependenciesOnly) {
    if (-not $PSScriptRoot -or -not (Test-Path (Join-Path $PSScriptRoot 'requirements-runtime.txt'))) {
        throw '-DependenciesOnly is available only from a Tag source checkout.'
    }
    $runtime = Join-Path $PSScriptRoot '.venv'
    $runtimePython = Join-Path $runtime 'Scripts/python.exe'
    if (-not (Test-Path $runtimePython)) {
        Write-Host 'Creating Tag runtime...'
        & python -m venv $runtime
        if ($LASTEXITCODE -ne 0) { throw "Tag runtime creation failed ($LASTEXITCODE)" }
    }
    Write-Host 'Installing pinned Tag runtime dependencies...'
    & $runtimePython -m pip install -r (Join-Path $PSScriptRoot 'requirements-runtime.txt')
    if ($LASTEXITCODE -ne 0) { throw "Tag dependency installation failed ($LASTEXITCODE)" }
    Write-Host 'Pinned Tag dependencies are installed.'
    exit 0
}
$installerArgs = @()
if ($Version) { $installerArgs += @('--version', $Version) }
if ($Channel) { $installerArgs += @('--channel', $Channel) }
if ($BinDir) { $installerArgs += @('--bin-dir', $BinDir) }
if ($SkipDependencies) { $installerArgs += '--skip-dependencies' }
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'scripts/tag_install.py'))) {
    if (-not $Version -and -not $Channel) { $installerArgs += @('--source', $PSScriptRoot) }
    & python (Join-Path $PSScriptRoot 'scripts/tag_install.py') @installerArgs
    if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
} else {
    $tagDownload = Join-Path ([IO.Path]::GetTempPath()) ('tag-bootstrap-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tagDownload | Out-Null
    try {
        $installer = Join-Path $tagDownload 'tag_install.py'
        $channels = Join-Path $tagDownload 'release-channels.json'
        Invoke-WebRequest 'https://raw.githubusercontent.com/klovr-co/tag/main/scripts/tag_install.py' -OutFile $installer
        Invoke-WebRequest 'https://raw.githubusercontent.com/klovr-co/tag/main/release-channels.json' -OutFile $channels
        & python $installer @installerArgs
        if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
    } finally {
        Remove-Item -LiteralPath $tagDownload -Recurse -Force
    }
}
