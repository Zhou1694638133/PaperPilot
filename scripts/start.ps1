param([ValidateSet('api', 'worker', 'web')][string]$Service = 'web')
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
if ($Service -eq 'web') {
    Set-Location -LiteralPath (Join-Path $projectPath 'frontend')
    & npm.cmd run dev
} else {
    Set-Location -LiteralPath (Join-Path $projectPath 'backend')
    $pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Missing Python environment. Run uv sync --locked --python 3.12 in backend first.'
    }
    if ($Service -eq 'api') {
        & $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    } else {
        & $pythonPath -m app.worker
    }
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
