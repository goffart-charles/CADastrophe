param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArgs)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$runtimePath = Join-Path $PSScriptRoot '.tools/mamba/envs/.occt-env'
$runtimeExecutable = Join-Path $runtimePath 'python.exe'
if (-not (Test-Path -LiteralPath $runtimeExecutable)) {
    throw 'Environnement local absent. Voir README.md pour installer environment.yml.'
}
$previousPath = $env:PATH
try {
    $env:PATH = "$runtimePath;$runtimePath/Library/bin;$runtimePath/Scripts;$previousPath"
    & $runtimeExecutable (Join-Path $PSScriptRoot 'main.py') @AppArgs
    exit $LASTEXITCODE
} finally {
    $env:PATH = $previousPath
}
