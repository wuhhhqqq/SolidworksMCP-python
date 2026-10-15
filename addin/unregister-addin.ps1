param([Parameter(Mandatory=$true)][string]$AssemblyPath)
$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $AssemblyPath).Path
$regasm = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe"
& $regasm $resolved /unregister
if ($LASTEXITCODE -ne 0) { throw "RegAsm unregister failed with exit code $LASTEXITCODE" }
Write-Host "Unregistered SolidWorks MCP add-in: $resolved" -ForegroundColor Green
