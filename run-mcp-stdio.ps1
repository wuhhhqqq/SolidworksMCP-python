# Pure stdio launcher for MCP clients.
# Do not write banners or diagnostics to stdout: it is reserved for MCP JSON-RPC.
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $scriptDir ".venv\Scripts\python.exe"
$sourceDir = Join-Path $scriptDir "src"

if (-not (Test-Path $python)) {
    [Console]::Error.WriteLine("SolidWorks MCP virtual environment was not found: $python")
    exit 1
}

$mutex = New-Object System.Threading.Mutex($false, "Local\SolidWorksMCP-stdio")
$mutexAcquired = $false
$exitCode = 0
try {
    try {
        $mutexAcquired = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        # An interrupted previous launcher left the mutex abandoned. The
        # current process can safely take ownership and continue.
        $mutexAcquired = $true
    }

    if (-not $mutexAcquired) {
        [Console]::Error.WriteLine(
            "Another SolidWorks MCP stdio process is already running. " +
            "Keep one persistent MCP server per SolidWorks instance."
        )
        $exitCode = 16
    }
    else {
        $env:PYTHONUTF8 = "1"
        if (Test-Path $sourceDir) {
            # Prefer the bundled source tree so an in-place MCP update takes effect
            # after the client restarts, even if its existing venv has an older wheel.
            $env:PYTHONPATH = if ($env:PYTHONPATH) {
                "$sourceDir;$env:PYTHONPATH"
            } else {
                $sourceDir
            }
        }
        & $python -m solidworks_mcp.server --mode local @ServerArgs
        $exitCode = $LASTEXITCODE
    }
}
finally {
    if ($mutexAcquired) {
        try {
            $mutex.ReleaseMutex()
        }
        catch {
            # The process is exiting; there is no safe recovery action here.
        }
    }
    $mutex.Dispose()
}

exit $exitCode
