<#
Start only the local FastAPI bridge used by the SolidWorks Task Pane.
This script is idempotent and never starts or stops SolidWorks itself.
#>
param([int]$Port = 8766)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

try {
    $health = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) -UseBasicParsing -TimeoutSec 2
    if ($health.StatusCode -eq 200) { exit 0 }
} catch { }

$env:PYTHONPATH = Join-Path $root "src"
$logDir = Join-Path $root ".solidworks_mcp\ui_logs"
try {
    New-Item -ItemType Directory -Path $logDir -Force -ErrorAction Stop | Out-Null
} catch {
    $logDir = Join-Path $env:TEMP "solidworks_mcp\ui_logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$stdoutLog = Join-Path $logDir "panel_server.out.log"
$stderrLog = Join-Path $logDir "panel_server.err.log"
$pythonArgs = @("-m", "uvicorn", "solidworks_mcp.ui.server:app", "--host", "127.0.0.1", "--port", "$Port")
Start-Process -FilePath $python -ArgumentList $pythonArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
