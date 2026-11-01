$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath (Join-Path $projectPath 'backend')
try {
    & uv sync --locked --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    & .\.venv\Scripts\python.exe -m pytest -q --basetemp ../data/pytest-temp
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
} finally { Pop-Location }
Push-Location -LiteralPath (Join-Path $projectPath 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }

