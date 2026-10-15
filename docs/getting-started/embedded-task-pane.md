# Embedded SolidWorks Task Pane

The repository now includes a SolidWorks 2019 COM add-in that hosts the complete
MCP tool surface inside a native Task Pane. The add-in is a thin WebBrowser host;
all model operations still execute in the Python adapter, so stdio MCP clients
and the embedded panel use the same tool implementations.

## Start the local bridge

From the repository root, run:

```powershell
.\run-panel.ps1
```

The script is idempotent and binds only to `127.0.0.1:8766`. It does not start or
stop SolidWorks and does not close documents. The panel page is available at
`http://127.0.0.1:8766/panel`.

## Explicitly start SolidWorks

The panel has a **启动 SolidWorks** button. It is an explicit action: normal
panel loading and normal MCP tool calls only attach to an existing instance.
When pressed, the bridge first checks the running COM instance, then uses the
configured `solidworks_path`/`SOLIDWORKS_MCP_SOLIDWORKS_PATH`, an installed
SolidWorks release, or a SolidWorks desktop shortcut. It waits for COM to
register and then connects. It never uses COM `Dispatch`, starts a second
instance when one is already running, or terminates SolidWorks during shutdown.

The same action is available to a local external tool while `run-panel.ps1` is
running:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8766/api/panel/start-solidworks `
  -ContentType application/json `
  -Body '{"timeout_seconds":120}'
```

For installations where automatic discovery is ambiguous, set the executable
explicitly before starting the bridge:

```powershell
$env:SOLIDWORKS_MCP_SOLIDWORKS_PATH = 'C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS 2019\SLDWORKS.exe'
```

This feature starts the desktop application process and waits for its COM
automation service. It does not install, repair, or start the SolidWorks
License Manager service; that service must already be installed and enabled by
the SolidWorks installation.

## Build and register the add-in

Install Visual Studio Build Tools with the .NET Framework 4.7.2 developer pack.
Set `SOLIDWORKS_INSTALL_DIR` to the SolidWorks installation directory and build:

```powershell
msbuild .\addin\SolidWorksMcpAddin\SolidWorksMcpAddin.csproj /p:Configuration=Release
```

Run an elevated PowerShell prompt to register the resulting DLL:

```powershell
.\addin\register-addin.ps1 -AssemblyPath .\addin\SolidWorksMcpAddin\bin\Release\net472\SolidWorksMcpAddin.dll
```

Restart SolidWorks 2019 and enable **SolidWorks MCP** under Tools > Add-Ins. The
add-in checks the lightweight health endpoint and starts `run-panel.ps1` only when
the bridge is not already running. To remove it:

```powershell
.\addin\unregister-addin.ps1 -AssemblyPath .\addin\SolidWorksMcpAddin\bin\Release\net472\SolidWorksMcpAddin.dll
```

## Panel API

The panel consumes these local endpoints:

- `GET /api/panel/tools`: all registered tools, categories, and JSON Schemas.
- `GET /api/panel/tools/{name}`: one tool definition.
- `POST /api/panel/tools/{name}/execute`: execute with `{ "arguments": {}, "confirmed": false }`.
- `GET /api/panel/health` and `GET /api/panel/state`: connection and adapter state.
- `GET /api/panel/logs`: recent operation results.
- `POST /api/panel/start-solidworks`: explicit local launch and COM attach.

The catalog is generated from FastMCP at runtime, so newly added tools appear in
the panel without a C# change. Destructive operations are marked and require an
explicit confirmation in the panel. The shutdown path disconnects COM only; it
never calls `ExitApp`, `QuitDoc`, or closes user documents.
