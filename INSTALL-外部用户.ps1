# SolidWorks MCP 外部用户安装脚本
# 用法：在解压后的项目根目录，以 PowerShell 执行：
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\INSTALL-外部用户.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

function Find-Python {
    $candidates = @(
        @("py", "-3.12"),
        @("py", "-3.11"),
        @("py", "-3"),
        @("python")
    )
    foreach ($candidate in $candidates) {
        try {
            if ($candidate.Count -gt 1) {
                $version = & $candidate[0] $candidate[1] --version 2>&1
            } else {
                $version = & $candidate[0] --version 2>&1
            }
            if ($LASTEXITCODE -eq 0 -and $version -match "Python 3\.(1[1-9]|[2-9][0-9])") {
                return $candidate
            }
        } catch {
            # Try the next Python launcher.
        }
    }
    throw "未找到 Python 3.11 或更高版本。请安装 python.org 的 64 位 Python。"
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($script:pythonLauncher.Count -gt 1) {
        & $script:pythonLauncher[0] @($script:pythonLauncher[1..($script:pythonLauncher.Count - 1)] + $Arguments)
    } else {
        & $script:pythonLauncher[0] @Arguments
    }
}

Write-Host "SolidWorks MCP 外部用户安装" -ForegroundColor Cyan
$script:pythonLauncher = Find-Python
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "创建虚拟环境..." -ForegroundColor Green
    Invoke-Python -m venv .venv
}

if (-not (Test-Path $venvPython)) {
    throw "虚拟环境创建失败：$venvPython"
}

Write-Host "安装/更新 MCP 运行依赖..." -ForegroundColor Green
& $venvPython -m ensurepip --upgrade
& $venvPython -m pip install --upgrade pip setuptools wheel
$bundledWheel = Join-Path $projectDir "solidworks_mcp_python-1.0.3-py3-none-any.whl"
if (Test-Path -LiteralPath $bundledWheel) {
    & $venvPython -m pip install --upgrade --force-reinstall --no-deps "$bundledWheel"
    if ($LASTEXITCODE -ne 0) { throw "MCP wheel 更新失败。" }
    & $venvPython -m pip install "$bundledWheel`[ui`]"
} else {
    & $venvPython -m pip install --no-deps --force-reinstall .
    if ($LASTEXITCODE -ne 0) { throw "MCP 源码安装失败。" }
    & $venvPython -m pip install ".[ui]"
}
if ($LASTEXITCODE -ne 0) { throw "MCP 安装失败，请检查上方 pip 错误。" }

& $venvPython -c "import solidworks_mcp, fastmcp; print('SolidWorks MCP 安装验证通过')"

Write-Host "" 
Write-Host "安装完成。下一步：" -ForegroundColor Green
Write-Host "1. 正常打开本机 SolidWorks。"
Write-Host "2. 将 mcp-config-stdio.json 中的项目路径替换为实际绝对路径。"
Write-Host "3. 把配置加入 Claude Desktop、Cursor、LM Studio 或其他 MCP 客户端。"
Write-Host "4. 完全重启 MCP 客户端。"
