# SolidWorks MCP Task Pane Add-in

This is a source-level SolidWorks 2019 COM add-in. It hosts the local MCP panel
inside a native Task Pane and keeps all CAD calls in the Python MCP process.

## Build

Install Visual Studio Build Tools with the `.NET Framework 4.7.2 developer pack`,
then set `SOLIDWORKS_INSTALL_DIR` to the SolidWorks installation directory (for
example `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS`) and run:

```powershell
msbuild .\SolidWorksMcpAddin\SolidWorksMcpAddin.csproj /p:Configuration=Release
```

The project targets x64 because SolidWorks 2019 is a 64-bit in-process COM host.
Keep the add-in and SolidWorks bitness identical. The registration helper uses
the 64-bit .NET Framework `RegAsm.exe` so SolidWorks can see the COM class.

## Register

Run `..\register-addin.ps1 -AssemblyPath <path-to-dll>` from an elevated PowerShell
prompt. The script uses the .NET Framework `RegAsm.exe` and enables automatic
loading. `unregister-addin.ps1` removes the same registration.

The add-in starts `run-panel.ps1` only if `http://127.0.0.1:8766/panel` is not
already reachable. It never calls `ExitApp`, `QuitDoc`, or closes user documents.
