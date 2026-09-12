param(
    [int]$Cases = 30,
    [int]$Workers = 1,
    [int]$Seed = 20260912,
    [string]$StrategyPath = '',
    [string]$RobotId = 'local',
    [int]$Port = 0,
    [string]$PythonExe = ''
)
$ErrorActionPreference = 'Stop'
if (-not $PythonExe) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    $venvPython = if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' } else { '' }
    if ($venvPython -and (Test-Path -LiteralPath $venvPython)) { $PythonExe = $venvPython }
    elseif (Test-Path -LiteralPath $bundledPython) { $PythonExe = $bundledPython }
    else { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
}
$simArgs = @((Join-Path $PSScriptRoot 'run.py'), 'batch', '--cases', "$Cases", '--workers', "$Workers", '--seed', "$Seed", '--robot-id', $RobotId, '--port', "$Port")
if ($StrategyPath) {
    $strategyResolved = (Resolve-Path -LiteralPath $StrategyPath).Path
    $simArgs += @('--cwd', (Split-Path -Parent $strategyResolved), '--command', $PythonExe, $strategyResolved)
}
$env:PYTHONIOENCODING = 'utf-8'
& $PythonExe @simArgs
exit $LASTEXITCODE
