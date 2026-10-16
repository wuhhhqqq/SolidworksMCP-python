# SolidWorks MCP 在总体目标架构中的定位

版本：1.0  日期：2026-10-16

SolidWorks MCP 位于总体目标架构的“工程专业工具集”区域，是变压器领域服务调用 SolidWorks 的受控执行器。HydroAgent 负责通用 Agent/Skill 和交互；变压器项目负责设计基线、参数映射、检查、审批和发布；本项目负责 SolidWorks 会话、模型/装配/工程图操作以及原生几何证据。

```text
HydroAgent Agent/Skill -> 变压器领域动作 -> SolidWorks 适配器/worker
 -> SolidWorks MCP stdio -> SolidWorks COM/API -> 模型、装配、工程图、BOM、测量证据
```

## 内部功能

| 内部模块 | 职责 |
|---|---|
| MCP 工具层 | 工具注册、参数校验、协议响应 |
| 能力与身份层 | SolidWorks 版本、后端、PID、连接归属和动作验证状态 |
| 会话与租约层 | 唯一写入者、文档锁、心跳、取消和失联状态 |
| 工作流层 | 建模、装配、工程图、BOM、测量和保存重开 |
| CAD 适配层 | COM/API、线程模型、弹窗、进程和版本差异 |
| 产物与证据层 | 文件哈希、测量、原始响应和回读证据 |

## 对外接口

| 接口 | 用途 |
|---|---|
| `solidworks_get_capabilities` | 返回版本、后端、PID、连接归属、支持动作和验证状态 |
| `solidworks_get_session` | 返回会话、租约、当前文档、配置和读写状态 |
| `solidworks_acquire_lease` / `solidworks_release_lease` | 获取或释放唯一写入租约 |
| `prepare_model_workspace` | 创建独立工作区并应用受控模板 |
| `create_or_open_model` | 创建/打开零件或装配并确认配置 |
| `apply_parameterized_template` | 按批准映射修改尺寸、材料和配置 |
| `read_geometry_evidence` | 返回包围盒、体积、质量、孔位、距离等原生测量 |
| `verify_assembly_interference` | 执行装配重建和干涉检查 |
| `create_engineering_drawing` | 创建工程图、视图、标注和 BOM |
| `save_reopen_verify` / `export_artifacts` | 保存重开回读并生成产物清单 |
| `solidworks_cancel_operation` | 请求取消并保留未知状态 |

统一结果必须包含 `task_id`、`run_id`、`operation_id`、`execution_status`、`engineering_status`、`evidence_status`、软件版本、输入哈希、产物和原始响应引用。`BUSY`、`TIMEOUT_UNKNOWN`、`UNKNOWN_NEEDS_RECONCILIATION` 不得自动重放写操作。

本项目不负责变压器工程 PASS、十二类检查最终判定、企业审批和发布。基准件、原生测量、装配干涉、工程图/BOM、保存关闭重开再测量和异常恢复必须逐项实机验收。
