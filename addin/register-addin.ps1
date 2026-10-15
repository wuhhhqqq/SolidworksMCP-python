param([Parameter(Mandatory=$true)][string]$AssemblyPath)
$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $AssemblyPath).Path
$regasm = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\RegAsm.exe"
if (-not (Test-Path -LiteralPath $regasm)) { throw "RegAsm.exe not found: $regasm" }
& $regasm $resolved /codebase /tlb
if ($LASTEXITCODE -ne 0) { throw "RegAsm failed with exit code $LASTEXITCODE" }
Write-Host "Registered SolidWorks MCP add-in: $resolved" -ForegroundColor Green
