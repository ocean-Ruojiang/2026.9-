[CmdletBinding()]
param(
    [string]$Python = '',
    [string[]]$Configs = @('quick'),
    [int]$Cases = 3,
    [int]$Workers = 1,
    [int]$Seed = 20260912,
    [ValidateSet(0,10,11,12,13,14,15,16)][int]$Count = 0,
    [ValidateSet('uniform','edge','clustered','mixed')][string]$Layout = 'uniform',
    [ValidateSet('random','minimum','maximum')][string]$RadiusMode = 'random',
    [ValidateSet('smooth','hash','zero')][string]$Noise = 'smooth',
    [string]$Output = ''
)
$ErrorActionPreference = 'Stop'
$codeDir = $PSScriptRoot
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $venvPython = if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' } else { '' }
    if ($venvPython -and (Test-Path -LiteralPath $venvPython)) { $Python = $venvPython }
    elseif (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
# Use the same interpreter for the launcher, simulator and every strategy worker.
& $Python -c 'import numpy'
if ($LASTEXITCODE -ne 0) {
    throw 'NumPy is missing. Install requirements.txt with the selected Python interpreter.'
}
$configPaths = @()
foreach ($config in $Configs) {
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        $configPaths += (Resolve-Path -LiteralPath $config).Path
    } else {
        $name = $config
        if (-not $name.EndsWith('.json')) { $name += '.json' }
        $configPaths += (Resolve-Path -LiteralPath (Join-Path (Join-Path $codeDir 'configs') $name)).Path
    }
}
$runArgs = @('-m','strategy2','local-sim','--configs') + $configPaths + @(
    '--cases',"$Cases",'--workers',"$Workers",'--seed',"$Seed",'--layout',$Layout,
    '--radius-mode',$RadiusMode,'--noise',$Noise
)
if ($Count -gt 0) { $runArgs += @('--count',"$Count") }
if ($Output) {
    $absoluteOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $runArgs += @('--output',$absoluteOutput)
}
Push-Location -LiteralPath $codeDir
try {
    & $Python @runArgs
    $runExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($runExit -ne 0) {
    throw "Experiment did not fully succeed (exit $runExit). Results and failure logs are preserved."
}
