param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$RuntimePath = $env:CONDA_PREFIX,
    [string]$PackPythonPath,
    [string]$IsccPath = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe",
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
function Invoke-BuildCommand {
    param([string]$Executable, [string[]]$CommandArguments)
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell represents native stderr as ErrorRecords, even
        # for successful commands. Preserve it and use the process exit code.
        $ErrorActionPreference = 'Continue'
        & $Executable @CommandArguments
        $commandExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($commandExit -ne 0) { throw "Échec de $Executable (code $commandExit)." }
}
$repoPath = Split-Path $PSScriptRoot -Parent
if (-not $Version) {
    $versionDefinition = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'version.iss') -Raw
    if ($versionDefinition -notmatch '\A\s*#define AppVersion "(\d+\.\d+\.\d+)"\s*\z') {
        throw 'Version invalide dans build/version.iss : attendu #define AppVersion "majeur.mineur.correctif".'
    }
    $Version = $Matches[1]
}
if (-not $RuntimePath) {
    $localRuntime = Join-Path $repoPath '.tools/mamba/envs/.occt-env'
    if (Test-Path -LiteralPath (Join-Path $localRuntime 'python.exe')) { $RuntimePath = $localRuntime }
    else { throw 'Indiquer -RuntimePath ou activer cadastrophe-build.' }
}
$RuntimePath = (Resolve-Path -LiteralPath $RuntimePath).Path
$runtimeExe = Join-Path $RuntimePath 'python.exe'
if (-not $PackPythonPath) {
    $PackPythonPath = $runtimeExe
    $localPackPython = Join-Path $repoPath '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath (Join-Path $RuntimePath 'Lib/site-packages/conda_pack')) -and
        (Test-Path -LiteralPath (Join-Path $repoPath '.venv/Lib/site-packages/conda_pack')) -and
        (Test-Path -LiteralPath $localPackPython)) {
        $PackPythonPath = $localPackPython
    }
}
$PackPythonPath = (Resolve-Path -LiteralPath $PackPythonPath).Path
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
foreach ($required in @($runtimeExe, $compilerPath)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Outil absent : $required" }
}
if (-not $SkipInstaller -and -not (Test-Path -LiteralPath $IsccPath)) {
    throw 'Installer Inno Setup 6 ou indiquer -IsccPath. -SkipInstaller prépare uniquement le paquet.'
}
$releasePath = Join-Path (Join-Path $PSScriptRoot 'out') $Version
if (Test-Path -LiteralPath $releasePath) { throw "Sortie déjà présente : $releasePath. Choisir une nouvelle version ou déplacer la sortie existante." }
$appPath = Join-Path $releasePath 'app'
New-Item -ItemType Directory -Path $appPath -Force | Out-Null
$previousPath = $env:PATH
try {
    $env:PATH = "$RuntimePath;$RuntimePath/Library/bin;$RuntimePath/Scripts;$previousPath"
    Invoke-BuildCommand $runtimeExe @('-B', (Join-Path $PSScriptRoot 'check_runtime.py'))
    Invoke-BuildCommand $PackPythonPath @('-B', (Join-Path $PSScriptRoot 'pack_runtime.py'), $RuntimePath, (Join-Path $releasePath 'runtime.zip'))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $releasePath 'runtime.zip'), (Join-Path $appPath 'runtime'))
    Invoke-BuildCommand $PackPythonPath @('-B', (Join-Path $PSScriptRoot 'stage_application.py'), $repoPath, $appPath)
    $launcherPath = Join-Path $appPath 'CADastrophe.exe'
    Invoke-BuildCommand $compilerPath @('/nologo', '/codepage:65001', '/target:winexe', '/platform:x64', '/reference:System.Windows.Forms.dll', "/out:$launcherPath", (Join-Path $PSScriptRoot 'Launcher.cs'))
    Invoke-BuildCommand $runtimeExe @('-B', (Join-Path $PSScriptRoot 'release_metadata.py'), $RuntimePath, $appPath, $Version)
    if (-not $SkipInstaller) {
        Invoke-BuildCommand $IsccPath @("/DAppVersion=$Version", "/DSourceDir=$appPath", "/DOutputDir=$releasePath", (Join-Path $PSScriptRoot 'installer.iss'))
    }
    Write-Host "Paquet prêt : $appPath"
} finally {
    $env:PATH = $previousPath
}
