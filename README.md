# SolidWorks CAD Assistant & MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-blue?logo=windows)](https://www.microsoft.com/windows)
[![SolidWorks](https://img.shields.io/badge/SolidWorks-2019--2026-red)](https://www.solidworks.com/)
[![Coverage](https://codecov.io/gh/andrewbartels1/SolidworksMCP-python/branch/main/graph/badge.svg)](https://codecov.io/gh/andrewbartels1/SolidworksMCP-python)

Python MCP server for SolidWorks automation with 255 tools, plus an optional agent/prompt-testing layer for AI-assisted workflows.

Current implementation boundaries, validation evidence, and remaining closure work:
[功能实现与闭环审计（2026-10-14）](docs/planning/functional-closure-audit-2026-10-14.md).
Tool registration counts do not imply real SolidWorks execution coverage.

Latest reliability update: [1.0.2 会话与启动收口](docs/user-guide/release-1.0.2.md).
Real COM automation is owned by one MCP process per Windows user; a second
stdio/panel process must wait for the owner to disconnect.

## MCP Client Package

The project is installable as the `solidworks-mcp-python` Python package and includes
a pure stdio launcher for MCP clients. Use `run-mcp-stdio.ps1` from the project
root; it attaches only to an already-running SolidWorks instance and never
launches or closes SolidWorks itself. The repository `.mcp.json` is configured
for this launcher.

Build distributable artifacts with:

```powershell
uv build
```

Use `run-mcp.ps1` only for the local development HTTP/demo workflow. It writes
human-readable startup output and is intentionally not used as an MCP stdio
entrypoint.

### DXF To SolidWorks Part

`import_dxf_to_part` creates a new native `.sldprt` from a basic DXF. It imports
model-space `LINE`, `ARC`, `CIRCLE`, `LWPOLYLINE`, and `POLYLINE` geometry into a
sketch on the selected Front, Top, or Right plane. Set `extrusion_depth_mm` to
create a solid; omit it to retain a sketch-only part. Coordinates are converted
from DXF units to millimetres.

Call `validate_dxf_geometry` to preflight a file without opening or changing
SolidWorks. It reports duplicate/overlapping linework, zero-size entities,
open and branching profiles, and non-endpoint line crossings. Import performs
the same validation by default: geometry errors stop the import, while an open,
branching, or intersecting profile stops an extrusion. Set
`validate_geometry=false` only when deliberately importing an unvalidated
sketch. Arc intersections are reported as an analysis limitation rather than
approximated, so an arc-containing DXF is not automatically cleared for
extrusion by the DXF layer. When `extrusion_depth_mm` is set, the imported
sketch is then checked by SolidWorks itself using `ISketch.CheckFeatureUse` for
a boss extrusion; native errors such as self-intersection, zero-length geometry,
and invalid contour combinations stop the extrusion. The standalone
`check_sketch_feature_use` tool exposes the same SolidWorks check for an
existing sketch and also supports base/surface/cut extrude contexts.

Direct `.dwg` input is intentionally rejected. Convert DWG first to a basic DXF
(for example, the `export_caxa_2015_dxf` R12 output) so proxy entities, blocks,
dimensions, and application-specific objects cannot create incomplete geometry.
By default unsupported DXF entities also produce an error; use `strict=false`
only when intentionally importing the supported geometry and ignoring the rest.

## Overview

> ⚠️ **Project Status:** This project is under active construction. Features, APIs, documentation, and setup steps may change as the Python and UI implementation is finalized. This is a hobby/research product, please feel free to make an issue if you have questions or feedback! ⚠️

This project focuses on practical SolidWorks automation with an AI-friendly loop:

1. describe intent
2. generate a plan
3. execute MCP tools
4. inspect results
5. iterate

It includes:

- core MCP runtime for SolidWorks tool execution
- COM/VBA routing and adapter safety wrappers
- tool coverage across modeling, sketching, drawing, analysis, export, automation, templates, and macros
- optional agent orchestration/testing utilities under `src/solidworks_mcp/agents/`

## Supported Today

- Windows + SolidWorks COM automation for the main CAD lifecycle.
- Modeling, sketching, drawing, analysis, export, automation, templates, and macro tools.
- Prefab UI preview sync from the active viewport as PNG.
- Optional embedded SolidWorks Task Pane that discovers all registered MCP tools,
  renders their JSON-Schema parameters, and runs them through the same local MCP runtime.

### Embedded Task Pane

Run `./run-panel.ps1` to start the local-only panel bridge at
`http://127.0.0.1:8766/panel`. The source add-in and registration helpers are in
[`addin/`](addin/); setup instructions are in
[the embedded Task Pane guide](docs/getting-started/embedded-task-pane.md). The
panel also exposes an explicit **启动 SolidWorks** action (and
`POST /api/panel/start-solidworks`) that launches an installed desktop instance
and waits for COM registration; ordinary panel loading and MCP calls remain
attach-only.

## Not Yet / Simulated

- Mock adapter output is simulated and should not be treated as engineering truth.
- Live 3D viewport streaming in the UI.
- Checkpoint-level interference validation in the UI runner.

## What Works (Verified Windows Setup)

This is the setup path validated end-to-end:

1. Install Python from python.org (Windows installer).
2. Enable **Add python.exe to PATH** during install.
3. Install this project into a local `.venv`.
4. Launch MCP from `.venv\Scripts\python.exe` (not from WSL).

When this is correct, startup logs show:

- `Platform: Windows`
- `SolidWorks COM interface is available`
- `Registered ... SolidWorks tools` (count varies as tools evolve)
- `Connected to SolidWorks`

## Requirements

- Windows 10/11 for real SolidWorks COM automation.
- Python 3.11+ from python.org.
- Git.
- SolidWorks installed and launched at least once.

Linux/WSL is useful for docs/tests/mock mode, but not for direct COM automation.

## Quick Start (Windows, python.org)

```powershell
git clone https://github.com/andrewbartels1/SolidworksMCP-python.git
cd SolidworksMCP-python

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e .
```

Start server manually:

```powershell
.\.venv\Scripts\python.exe -m solidworks_mcp.server
```

Or use the helper script (open SolidWorks first):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-mcp.ps1 --real --year 2026
```

> **Mock mode warning** — running `run-mcp.ps1` without `--real` starts the
> server in mock mode.  All tool responses are simulated; nothing touches
> SolidWorks.  Always pass `--real --year <year>` for live automation.

## Development Commands

Use the helper script for common workflows:

```powershell
.\dev-commands.ps1
```

Common commands:

- `dev-install` - install/update local dev environment
- `dev-test` - run standard test suite (CI-safe subset)
- `dev-test-full` - run full test suite (includes smoke/integration paths)
- `dev-lint` - lint checks
- `dev-format` - format code
- `dev-docs-build` - build docs site once
- `dev-docs-strict` - strict docs build (fails on warnings)
- `dev-docs-audit` - generate docs audit report in `.generated/docs`

### Local CI Replica (Docker)

To mirror GitHub Actions CI locally (Ubuntu + conda env from `solidworks_mcp.yml` + `make test`), run:

```powershell
.\run-ci-local.ps1
```

The first run builds the image. Re-run without rebuild when only executing tests:

```powershell
.\run-ci-local.ps1 -NoBuild
```

## VS Code MCP Configuration (Windows)

Set your user MCP config (`%APPDATA%\Code\User\mcp.json`) to:

```json
{
  "servers": {
    "solidworks-mcp-server": {
      "type": "stdio",
      "command": "powershell",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\path\\to\\SolidworksMCP-python\\run-mcp-stdio.ps1"
      ]
    }
  },
  "inputs": []
}
```

Replace the script path with your local repository path. Start SolidWorks normally before connecting the MCP client.

## LM Studio MCP Configuration (Windows)

Set your LM Studio MCP config file to include this server (LM Studio expects `mcpServers`):

```json
{
  "mcpServers": {
    "solidworks-mcp-server": {
      "command": "powershell",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\path\\to\\SolidworksMCP-python\\run-mcp-stdio.ps1"
      ]
    }
  }
}
```

Alternative direct-python entry:

```json
{
  "mcpServers": {
    "solidworks-mcp-server": {
      "command": "C:\\Users\\andre\\OneDrive\\Documents\\GitHub\\SolidworksMCP-python\\.venv\\Scripts\\python.exe",
      "args": ["-m", "solidworks_mcp.server"]
    }
  }
}
```

After saving, restart LM Studio so it reloads MCP servers.

## Common Windows Fixes

If `python` is not found:

```powershell
python --version
```

If this opens Microsoft Store or fails, reinstall Python from python.org and enable PATH.

If startup fails with `ModuleNotFoundError: solidworks_mcp`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

If startup fails with `ModuleNotFoundError: fastmcp`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

## Docs

- Main docs site: <https://andrewbartels1.github.io/SolidworksMCP-python/>
- Home/overview: [docs/index.md](docs/index.md)

Key docs sections:

- Getting Started: [docs/getting-started](docs/getting-started)
- MCP Server Guide: [docs/user-guide](docs/user-guide)
- Tool Catalog: [docs/user-guide/tool-catalog](docs/user-guide/tool-catalog)
- Agents and Skills: [docs/agents](docs/agents)
- Planning/Roadmap: [docs/planning](docs/planning)

Direct links:

- [Installation](docs/getting-started/installation.md)
- [Quick Start](docs/getting-started/quickstart.md)
- [Tutorial: U-Joint Assembly Build](docs/getting-started/tutorials/u-joint-assembly-build.md)
- [Tutorial Tracks](docs/getting-started/tutorial-tracks.md)
- [Prefab UI Dashboard](docs/getting-started/prefab-ui-dashboard.md)
- [VS Code MCP Setup](docs/getting-started/vscode-mcp-setup.md)
- [Architecture](docs/user-guide/architecture.md)
- [Agents and Prompt Testing](docs/agents/agents-and-testing.md)
- [PydanticAI and Schemas](docs/agents/pydantic-ai-and-schemas.md)

## License

MIT License. See [LICENSE](LICENSE).
