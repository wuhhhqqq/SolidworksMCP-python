# SolidWorks MCP 外部用户安装说明

## 运行条件

- Windows 10/11
- 本机安装 SolidWorks，并且当前用户有权限启动它
- 64 位 Python 3.11 或更高版本（推荐 python.org 的 Python 3.12）
- 外部 MCP 客户端与 SolidWorks 位于同一台 Windows 电脑

本 MCP 通过 Windows COM 连接本机 SolidWorks。它不是远程桌面服务，不能直接控制另一台电脑上的 SolidWorks。

## 安装

1. 解压本 ZIP 到不含特殊权限限制的固定目录，例如 `C:\Apps\SolidworksMCP-python`。
2. 正常从桌面快捷方式打开一次 SolidWorks。
3. 在项目根目录打开 PowerShell，执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\INSTALL-外部用户.ps1
```

如果系统没有 `py` 命令，请先安装 Python 3.11+，安装时勾选 **Add Python to PATH**。

## 外部 MCP 客户端配置

复制 `mcp-config-stdio.json` 的内容，替换其中的项目绝对路径。通用格式如下：

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "powershell.exe",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:\\Apps\\SolidworksMCP-python\\run-mcp-stdio.ps1"
      ],
      "env": {
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

VS Code 的配置文件使用顶层 `servers`，并增加 `"type": "stdio"`；其他客户端通常使用顶层 `mcpServers`。具体格式以客户端文档为准。

配置后完全退出并重启外部客户端。每次 MCP 服务启动前，建议先打开 SolidWorks；MCP 会连接已有实例，不负责替用户启动桌面程序。

## 必须保持一个常驻 MCP 进程

stdio MCP 是一个长连接：外部客户端应在启动会话时启动一次
`run-mcp-stdio.ps1`，然后在同一个进程上发送后续所有工具调用。不要把
PowerShell 启动命令写进“每次工具调用执行”的脚本，也不要为每个工具调用
单独创建一个 MCP server 进程。

可以用下面的 PowerShell 命令检查是否只运行了一个 MCP 进程：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -match 'solidworks_mcp.server' } |
  Select-Object ProcessId, CommandLine
```

正常情况下，在一个外部客户端会话期间应只有一条对应的
`solidworks_mcp.server --mode local` 记录。若每次调用都出现新的 PID，说明外部
客户端配置方式不正确；请删除重复配置，只保留一个 stdio server 配置后重启客户端。
同时检查客户端的用户级、项目级和插件级 MCP 配置，避免同一个项目同时登记了
两个名称不同但都指向 `run-mcp-stdio.ps1` 的服务器。

MCP 已在单个进程内对 `OpenDoc6`、`NewPart` 和检查点/预览操作加锁，并会复用
已打开的同一路径文档。`run-mcp-stdio.ps1` 还会用 Windows 命名互斥锁拒绝第二个
并发 stdio 实例；这能阻止误配置继续操作 SolidWorks，但不能把多个客户端会话合并成一个
MCP 连接，所以仍应只保留一个配置。

注意：`create_part` 的含义是“明确新建一个零件”，它不是幂等工具。智能体要继续编辑
已有零件时，应先调用 `open_model` 或激活已列出的文档；不要在每个步骤都重复调用
`create_part`。

## 重要说明

- 给 MCP 客户端配置 `run-mcp-stdio.ps1`，不要配置用于人工调试的 `run-mcp.ps1`。
- 外部客户端重启、断开或停止 MCP 时，MCP 默认只断开 COM，不会关闭或删除已创建的 SolidWorks 文档；需要保存时仍应让智能体显式调用保存工具。
- `.venv` 不包含在分发包中，每台电脑必须独立安装依赖。
- 修改源码后，重新运行 `INSTALL-外部用户.ps1` 即可更新当前虚拟环境。
- 不要在 MCP 客户端正在运行时删除或重建 `.venv`，否则正在使用的 Python 文件可能被占用。

## 手工验证

```powershell
.\.venv\Scripts\python.exe -c "import solidworks_mcp, fastmcp; print('OK')"
```

看到 `OK` 后即可重启 MCP 客户端并检查工具列表。
