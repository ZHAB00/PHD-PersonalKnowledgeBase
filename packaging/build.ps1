param(
    [string]$Version = "0.1.0",
    [switch]$BundleModel,
    [string]$ISCC = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not $Python) {
    $Python = if ($env:PDH_PKG_PYTHON) { $env:PDH_PKG_PYTHON } else { "python" }
}

$OpenSSLDir = $env:PDH_PKG_OPENSSL_DIR
if (-not $OpenSSLDir) {
    $pythonExe = (Get-Command $Python -ErrorAction SilentlyContinue).Source
    if ($pythonExe -and (Test-Path $pythonExe)) {
        $OpenSSLDir = Join-Path (Split-Path (Split-Path $pythonExe -Parent) -Parent) "Library\bin"
    }
}
if (-not $OpenSSLDir) {
    $SslArgs = @()
    Write-Host "==> 未找到 OpenSSL DLL，跳过附加 ssl/crypto"
} else {
    $SslArgs = @(
        "--add-binary", "$OpenSSLDir\libssl-3-x64.dll;.",
        "--add-binary", "$OpenSSLDir\libcrypto-3-x64.dll;."
    )
}

Write-Host "==> 安装构建依赖"
& $Python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if ($BundleModel) {
    Write-Host "==> 下载内置向量模型（ONNX）到 packaging/resources/models"
    $modelDir = "packaging/resources/models"
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    $env:HF_ENDPOINT = "https://hf-mirror.com"
    $env:HF_HUB_DISABLE_XET = "1"
    & $Python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Qdrant/bge-small-zh-v1.5', cache_dir=r'$modelDir')"
    if ($LASTEXITCODE -ne 0) { throw "model download failed" }
}

Write-Host "==> PyInstaller 打包"
$appName = "PDH-PKG"
& $Python -m PyInstaller --noconfirm --clean --onedir --name $appName `
    --paths . `
    --distpath dist2 `
    --workpath build2 `
    --noconsole `
    --icon "packaging/resources/icon.ico" `
    --hidden-import fastembed `
    --hidden-import onnxruntime `
    --hidden-import langchain_ollama `
    --hidden-import rank_bm25 `
    --hidden-import jieba `
    --hidden-import neo4j `
    --hidden-import webview `
    @SslArgs `
    --add-data "app/templates;app/templates" `
    --add-data "app/static;app/static" `
    packaging/run.py
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

$dist = "dist2/$appName"

Write-Host "==> 拷贝 Qdrant"
if (Test-Path "qdrant") {
    Copy-Item -Recurse -Force "qdrant" "$dist/qdrant"
}

Write-Host "==> 清理 Qdrant 运行数据（避免把个人数据带进安装包）"
$runtimeDirs = @("$dist/storage", "$dist/snapshots", "$dist/qdrant/storage", "$dist/qdrant/snapshots")
foreach ($dir in $runtimeDirs) {
    if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}

if ($BundleModel -and (Test-Path "packaging/resources/models")) {
    Write-Host "==> 拷贝内置模型"
    Copy-Item -Recurse -Force "packaging/resources/models" "$dist/models"
}

Write-Host "==> Inno Setup 生成安装器"
$iscc = $ISCC
if (-not $iscc) {
    $candidates = @(
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Host "未找到 ISCC.exe，跳过安装器生成；dist/$appName 已可用"
    exit 0
}

& $iscc "/DMyAppVersion=$Version" "packaging/installer.iss"
if ($LASTEXITCODE -ne 0) { throw "installer build failed" }

Write-Host "完成：output/PDH-PKG-Setup-$Version.exe"
