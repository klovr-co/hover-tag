# Copyright 2026 klovr.co
# SPDX-License-Identifier: Apache-2.0
# Windows counterpart of install.sh: needs only Windows PowerShell 5.1, never a
# system Python. It downloads a pinned, checksum-verified uv, uses it to
# prepare a private Python, then runs the shared Python installer.
# TAG_INSTALL_PROGRESS=jsonl adds "@tag-progress {...}" lines for desktop apps.
[CmdletBinding()]
param(
    [string]$Version,
    [ValidateSet('stable', 'beta', 'alpha', 'edge')]
    [string]$Channel,
    [string]$BinDir,
    [switch]$SkipDependencies,
    [switch]$DependenciesOnly,
    # Print the prepared Python and uv paths, one per line, and exit.
    [switch]$RuntimeInfo
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Started from PowerShell 7 (pwsh), Windows PowerShell inherits pwsh's module
# path and can't load its own built-in commands such as Get-FileHash. Use
# Windows PowerShell's own module folders.
if ($PSVersionTable.PSEdition -ne 'Core') {
    $env:PSModulePath = @(
        (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WindowsPowerShell\Modules'),
        (Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules'),
        (Join-Path $PSHOME 'Modules')
    ) -join ';'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Write-TagProgress([string]$Step) {
    if ($env:TAG_INSTALL_PROGRESS -eq 'jsonl') {
        [Console]::Error.WriteLine('@tag-progress {"schema_version":1,"step":"' + $Step + '"}')
    }
}

function Get-TagHome {
    if ($env:TAG_HOME) {
        if (-not [IO.Path]::IsPathRooted($env:TAG_HOME)) { throw 'TAG_HOME must be an absolute path.' }
        return $env:TAG_HOME
    }
    $local = $env:LOCALAPPDATA
    if (-not $local -or -not [IO.Path]::IsPathRooted($local)) { $local = Join-Path $HOME 'AppData\Local' }
    return Join-Path $local 'Tag'
}

# Windows PowerShell 5.1 turns a native command's stderr into terminating
# errors under 'Stop'; run tools with 'Continue' and check exit codes instead.
function Invoke-Native([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command } finally { $ErrorActionPreference = $previous }
}

function Test-UvVersion([string]$Path, [string]$Expected) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try { $reported = (Invoke-Native { & $Path --version 2>$null }) -split ' ' } catch { return $false }
    return ($reported.Count -ge 2 -and $reported[1] -eq $Expected)
}

function Initialize-TagRuntime {
    $tagHome = Get-TagHome
    $uvVersion = '0.12.19'
    $pythonVersion = '3.12.14'
    $uv = $null
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found -and (Test-UvVersion $found.Source $uvVersion)) { $uv = $found.Source }
    if (-not $uv) {
        $uv = Join-Path $tagHome "runtime\uv\$uvVersion\uv.exe"
        if (-not (Test-UvVersion $uv $uvVersion)) {
            # Fixed hashes come from the corresponding upstream GitHub release assets.
            switch ($env:PROCESSOR_ARCHITECTURE) {
                'AMD64' { $target = 'x86_64-pc-windows-msvc'; $sha = '6dbb02d79e419522f1c500f0adb1cddcff0cda7d59b0d66ea7f5e3b4a1b2f5f0' }
                'ARM64' { $target = 'aarch64-pc-windows-msvc'; $sha = '115b54cb823bc48260670f5782001add6067ac8d98d18c8263a833704e287de9' }
                default { throw "No managed runtime for this platform ($env:PROCESSOR_ARCHITECTURE)." }
            }
            $dir = Split-Path -Parent $uv
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $stage = Join-Path $dir ('.download.' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $stage | Out-Null
            try {
                Write-TagProgress 'tools'
                [Console]::Error.WriteLine('Downloading Tag installation tools...')
                $archive = Join-Path $stage 'uv.zip'
                Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-$target.zip" -OutFile $archive
                if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne $sha) {
                    throw 'Installation tools checksum mismatch; retry installation.'
                }
                Expand-Archive -LiteralPath $archive -DestinationPath $stage
                $candidate = Join-Path $stage 'uv.exe'
                if (-not (Test-UvVersion $candidate $uvVersion)) { throw 'The downloaded installation tools cannot run on this computer.' }
                Move-Item -Force -LiteralPath $candidate -Destination $uv
            } finally {
                Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    # Reuse an exact compatible uv-managed runtime, including an existing cache.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $tagHome 'runtime\python'
    $python = Invoke-Native { & $uv python find --no-config --managed-python --no-python-downloads $pythonVersion 2>$null }
    if (-not $python) {
        Write-TagProgress 'python'
        [Console]::Error.WriteLine("Preparing Tag Python $pythonVersion...")
        $env:UV_PYTHON_BIN_DIR = Join-Path $tagHome 'runtime\bin'
        Invoke-Native { & $uv python install --no-config --no-bin $pythonVersion 2>&1 | ForEach-Object { [Console]::Error.WriteLine("$_") } }
        if ($LASTEXITCODE -ne 0) { throw "Tag Python preparation failed ($LASTEXITCODE)" }
        $python = Invoke-Native { & $uv python find --no-config --managed-python --no-python-downloads $pythonVersion }
    }
    $python = "$python".Trim()
    Invoke-Native { & $python -c 'import sys; assert sys.version_info[:3] == (3, 12, 14)' }
    if ($LASTEXITCODE -ne 0) { throw 'The prepared Tag Python is not the expected version.' }
    $env:TAG_BOOTSTRAP_PYTHON = $python
    $env:TAG_BOOTSTRAP_UV = $uv
    return @($python, $uv)
}

$runtime = Initialize-TagRuntime
$python, $uv = $runtime
if ($RuntimeInfo) {
    Write-Output $python
    Write-Output $uv
    exit 0
}
if ($DependenciesOnly) {
    if (-not $PSScriptRoot -or -not (Test-Path (Join-Path $PSScriptRoot 'requirements-runtime.txt'))) {
        throw '-DependenciesOnly is available only from a Tag source checkout.'
    }
    $venv = Join-Path $PSScriptRoot '.venv'
    $runtimePython = Join-Path $venv 'Scripts/python.exe'
    if (-not (Test-Path $runtimePython)) {
        Write-Host 'Creating Tag runtime...'
        Invoke-Native { & $uv venv --python $python $venv }
        if ($LASTEXITCODE -ne 0) { throw "Tag runtime creation failed ($LASTEXITCODE)" }
    }
    Write-Host 'Installing pinned Tag runtime dependencies...'
    Invoke-Native { & $uv pip install --python $runtimePython -r (Join-Path $PSScriptRoot 'requirements-runtime.txt') }
    if ($LASTEXITCODE -ne 0) { throw "Tag dependency installation failed ($LASTEXITCODE)" }
    Write-Host 'Preparing the local MFS embedding model...'
    Invoke-Native { & $runtimePython (Join-Path $PSScriptRoot 'scripts/preload_mfs_model.py') }
    if ($LASTEXITCODE -ne 0) { throw "MFS embedding model preparation failed ($LASTEXITCODE)" }
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
    # The installer writes progress to stderr; never let PowerShell treat that as failure.
    Invoke-Native { & $python (Join-Path $PSScriptRoot 'scripts/tag_install.py') @installerArgs }
    if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
} else {
    $tagDownload = Join-Path ([IO.Path]::GetTempPath()) ('tag-bootstrap-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tagDownload | Out-Null
    try {
        $installer = Join-Path $tagDownload 'tag_install.py'
        $channels = Join-Path $tagDownload 'release-channels.json'
        Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/klovr-co/hover-tag/main/scripts/tag_install.py' -OutFile $installer
        Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/klovr-co/hover-tag/main/release-channels.json' -OutFile $channels
        Invoke-Native { & $python $installer @installerArgs }
        if ($LASTEXITCODE -ne 0) { throw "TAG installation failed ($LASTEXITCODE)" }
    } finally {
        Remove-Item -LiteralPath $tagDownload -Recurse -Force
    }
}
