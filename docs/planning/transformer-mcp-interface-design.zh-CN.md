# SolidWorks MCP 在总体目标架构中的定位

版本：1.1  日期：2026-10-18

SolidWorks MCP 是变压器领域服务调用 SolidWorks 的受控执行器。变压器设计智能体负责通用 Agent/Skill 和交互；变压器项目负责设计基线、参数映射、十二类检查、审批和发布；本项目负责 SolidWorks 会话、模型/装配/工程图操作以及原生几何证据。

```text
变压器设计智能体 -> 变压器领域动作 -> SolidWorks 适配器/worker
 -> SolidWorks MCP stdio -> SolidWorks COM/API -> 模型、装配、工程图、BOM、测量证据
```

## 内部功能

| 模块 | 职责 |
|---|---|
| MCP 工具层 | 工具注册、参数校验、协议响应 |
| 能力与身份层 | SolidWorks 版本、后端、PID、连接归属和动作验证状态 |
| 会话与租约层 | 唯一写入者、文档锁、心跳、取消和失联状态 |
| 工作流层 | 建模、装配、工程图、BOM、测量和保存重开 |
| CAD 适配层 | COM/API、线程模型、弹窗、进程和版本差异 |
| 产物与证据层 | 文件哈希、测量、原始响应和回读证据 |

## 对外接口

`solidworks_get_capabilities`、`solidworks_get_session`、`solidworks_acquire_lease`、`solidworks_release_lease`、`prepare_model_workspace`、`create_or_open_model`、`apply_parameterized_template`、`read_geometry_evidence`、`verify_assembly_interference`、`create_engineering_drawing`、`save_reopen_verify`、`export_artifacts`、`solidworks_cancel_operation`。

## 实现状态

上述接口包含已实现的底层 MCP 能力、部分实现的业务封装和计划实现的变压器高层动作，不能仅凭接口名称视为已完成。每次结果必须区分 `implementation_status`（SIMULATION/RUNTIME_REPORTED/VERIFIED_ACTION/UNKNOWN）、`execution_status`、`engineering_status` 和 `evidence_status`。未完成基准件、原生测量、保存重开和异常恢复验收的动作不得标记为 VERIFIED_ACTION。SolidWorks MCP 不负责工程 PASS、十二类检查最终判定、审批或发布。
