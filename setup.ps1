# PDH-PKG 一键初始化脚本
# 用法:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python "E:\path\to\python.exe"
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipQdrant

param(
    [string]$Python = "",
    [switch]$SkipDeps,
    [switch]$SkipQdrant,
    [string]$QdrantVersion = "v1.19.0"
)

$ErrorActionPreference = "Stop"
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $Root

if (-not $Python) {
    if ($env:PDH_PKG_PYTHON) {
        $Python = $env:PDH_PKG_PYTHON
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = "python"
    } else {
        throw "未找到 Python。请安装 Python 3.10+，或通过 -Python 指定解释器路径。"
    }
}

Write-Host "==> 使用 Python: $Python"

# 1. 数据目录与 .env
New-Item -ItemType Directory -Force -Path "data" | Out-Null
if (Test-Path ".env") {
    Write-Host "==> .env 已存在，保留现有配置"
} else {
    Copy-Item ".env.example" ".env"
    Write-Host "==> 已从 .env.example 创建 .env，请编辑填写 DEEPSEEK_API_KEY 等配置"
}

# 2. Python 依赖
if ($SkipDeps) {
    Write-Host "==> 跳过 Python 依赖安装"
} else {
    Write-Host "==> 安装 Python 依赖"
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
}

# 3. Qdrant
function Test-Port {
    param([string]$HostName, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1000)) {
            $client.EndConnect($iar)
            return $true
        }
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
    return $false
}

function Install-Qdrant {
    param([string]$Version)
    if (-not $Version.StartsWith("v")) {
        $Version = "v" + $Version
    }
    $url = "https://github.com/qdrant/qdrant/releases/download/$Version/qdrant-x86_64-pc-windows-msvc.zip"
    $tempZip = Join-Path $env:TEMP "pdh-pkg-qdrant-$Version.zip"
    $tempDir = Join-Path $env:TEMP "pdh-pkg-qdrant-$Version"
    Write-Host "==> 下载 Qdrant $Version"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tempZip -UseBasicParsing
        if (Test-Path -LiteralPath $tempDir) {
            Remove-Item -LiteralPath $tempDir -Recurse -Force
        }
        Expand-Archive -Path $tempZip -DestinationPath $tempDir -Force
        $exe = Get-ChildItem -Path $tempDir -Filter "qdrant.exe" -Recurse -File | Select-Object -First 1
        if (-not $exe) { throw "压缩包中未找到 qdrant.exe" }
        New-Item -ItemType Directory -Force -Path "qdrant" | Out-Null
        Copy-Item -LiteralPath $exe.FullName -Destination "qdrant\qdrant.exe" -Force
        Write-Host "==> Qdrant 已安装到 qdrant\qdrant.exe"
    } catch {
        Write-Warning "Qdrant 自动下载失败: $($_.Exception.Message)"
        Write-Host "手动安装：下载 $url 后解压，将 qdrant.exe 放到 qdrant\qdrant.exe"
    } finally {
        if (Test-Path -LiteralPath $tempZip) { Remove-Item -LiteralPath $tempZip -Force }
        if (Test-Path -LiteralPath $tempDir) { Remove-Item -LiteralPath $tempDir -Recurse -Force }
    }
}

if ($SkipQdrant) {
    Write-Host "==> 跳过 Qdrant 下载"
} elseif (Test-Path "qdrant\qdrant.exe") {
    Write-Host "==> qdrant\qdrant.exe 已存在"
} elseif (Test-Path "qdrant.exe") {
    Write-Host "==> 检测到 qdrant.exe，后端会自动使用"
} else {
    Install-Qdrant -Version $QdrantVersion
}

# 4. 可选服务检查
Write-Host ""
Write-Host "==> 可选服务检查"
if (Test-Port "127.0.0.1" 6379) {
    Write-Host "  Redis    : 已运行"
} else {
    Write-Host "  Redis    : 未运行（可选，未启动时使用内存回退）"
}
if (Test-Port "127.0.0.1" 11434) {
    Write-Host "  Ollama   : 已运行"
} else {
    Write-Host "  Ollama   : 未运行（可选，默认向量模型为内置本地模型）"
}
if (Test-Port "127.0.0.1" 6333) {
    Write-Host "  Qdrant   : 已运行"
} else {
    Write-Host "  Qdrant   : 未运行（启动后端时会自动拉起 qdrant\qdrant.exe）"
}

Write-Host ""
Write-Host "==> 初始化完成"
Write-Host "下一步："
Write-Host "  1. 编辑 .env，填入 DEEPSEEK_API_KEY（使用云端对话时必填）"
Write-Host "  2. 启动调试服务: powershell -ExecutionPolicy Bypass -File packaging\debug.ps1"
Write-Host "  3. 浏览器打开: http://127.0.0.1:8001"
Write-Host "  4. 在设置页配置对话模型、向量模型、联网搜索和 Neo4j 后测试连接"