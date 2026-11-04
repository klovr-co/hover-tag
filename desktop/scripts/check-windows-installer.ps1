# Install, update and uninstall Tag.app's NSIS build the way a person does.
# A per-user install goes to %LOCALAPPDATA%\Tag, which is also where the Tag
# runtime lives, so each step must leave the runtime and its Tags in place.
param([string]$Bundle = 'src-tauri/target/debug/bundle/nsis')
$ErrorActionPreference = 'Stop'

$setup = Get-ChildItem -LiteralPath $Bundle -Filter '*-setup.exe' | Select-Object -First 1
if (-not $setup) { throw "No NSIS installer in $Bundle." }
$root = Join-Path $env:LOCALAPPDATA 'Tag'
# The installer keeps Cargo's binary name, tag-desktop.exe.
$app = Join-Path $root 'tag-desktop.exe'

# Stand-ins for what install.ps1 puts in the same folder.
$runtime = @('bin\tag.cmd', 'home\current.json', 'releases\0.0.0\VERSION')
foreach ($item in $runtime) {
    $path = Join-Path $root $item
    New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
    Set-Content -LiteralPath $path -Value "runtime file $item" -Encoding ascii
}

function Invoke-Step([string]$Name, [string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$Name exited with $($process.ExitCode)." }
}

function Assert-Runtime([string]$After) {
    foreach ($item in $runtime) {
        $path = Join-Path $root $item
        if (-not (Test-Path -LiteralPath $path) -or (Get-Content -LiteralPath $path) -ne "runtime file $item") {
            Get-ChildItem -LiteralPath $root -Recurse | Select-Object -ExpandProperty FullName
            throw "$After removed or changed the runtime file $item."
        }
    }
    Write-Host "${After}: the runtime is unchanged."
}

function Assert-App([bool]$Installed, [string]$After) {
    if ((Test-Path -LiteralPath $app) -ne $Installed) {
        Get-ChildItem -LiteralPath $root -Recurse | Select-Object -ExpandProperty FullName
        throw "$After left tag-desktop.exe $(if ($Installed) { 'missing' } else { 'in place' })."
    }
}

Write-Host "Installer: $($setup.Name)"
Invoke-Step 'Install' $setup.FullName @('/S')
Assert-App $true 'Install'
Assert-Runtime 'Install'

# An app update runs the new installer with /UPDATE over the installed app.
Invoke-Step 'Update' $setup.FullName @('/S', '/UPDATE')
Assert-App $true 'Update'
Assert-Runtime 'Update'

# _?= keeps the uninstaller in place so this waits until it finishes.
Invoke-Step 'Uninstall' (Join-Path $root 'uninstall.exe') @('/S', "_?=$root")
Assert-App $false 'Uninstall'
Assert-Runtime 'Uninstall'
