$ErrorActionPreference = 'Stop'
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & $python.Source "$PSScriptRoot\app.py"
    exit $LASTEXITCODE
}
$bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundled) {
    & $bundled "$PSScriptRoot\app.py"
    exit $LASTEXITCODE
}
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3 "$PSScriptRoot\app.py"
    exit $LASTEXITCODE
}
throw '未找到 Python 3.11+。请先安装 Python。'
