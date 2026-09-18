# Copyright 2026 Open Tag contributors
# SPDX-License-Identifier: Apache-2.0
[CmdletBinding()]
param([string]$Version, [string]$BinDir, [switch]$SkipDependencies)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ is required. Install Python and enable its PATH option.'
}
$installerArgs = @()
if ($Version) { $installerArgs += @('--version', $Version) }
if ($BinDir) { $installerArgs += @('--bin-dir', $BinDir) }
if ($SkipDependencies) { $installerArgs += '--skip-dependencies' }
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'scripts/tag_install.py'))) {
    if (-not $Version) { $installerArgs += @('--source', $PSScriptRoot) }
    & python (Join-Path $PSScriptRoot 'scripts/tag_install.py') @installerArgs
    if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
} else {
    $tagDownload = Join-Path ([IO.Path]::GetTempPath()) ('tag-bootstrap-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tagDownload | Out-Null
    try {
        $installer = Join-Path $tagDownload 'tag_install.py'
        Invoke-WebRequest 'https://raw.githubusercontent.com/klovr-co/tag/main/scripts/tag_install.py' -OutFile $installer
        & python $installer @installerArgs
        if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
    } finally {
        Remove-Item -LiteralPath $tagDownload -Recurse -Force
    }
}
