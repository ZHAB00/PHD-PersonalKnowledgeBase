$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = $Root
$env:PDH_PKG_DEBUG = "1"

function Test-PythonUsable {
    param([string]$PythonPath)
    if (-not (Test-Path $PythonPath)) { return $false }
    try {
        & $PythonPath -c "import neo4j, uvicorn" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$Python = ""
if ($env:PDH_PKG_PYTHON -and (Test-PythonUsable $env:PDH_PKG_PYTHON)) {
    $Python = $env:PDH_PKG_PYTHON
} elseif ($env:CONDA_PREFIX -and (Test-PythonUsable (Join-Path $env:CONDA_PREFIX "python.exe"))) {
    $Python = Join-Path $env:CONDA_PREFIX "python.exe"
} elseif (Test-PythonUsable "E:\anaconda3\envs\enterprise_kb\python.exe") {
    $Python = "E:\anaconda3\envs\enterprise_kb\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} else {
    throw "未找到可用 Python"
}

Write-Host "使用 Python: $Python"
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
