# Transformer Design Workbench Integration Design

Status: design supplement, version 0.1
Date: 2026-10-15

配套架构图：[SolidWorks MCP 架构设计](./transformer-mcp-architecture.html)；内部功能和对外接口：[接口设计](./transformer-mcp-interface-design.zh-CN.md)。

This document defines how SolidWorks MCP participates in the transformer AI design workbench together with CAXA MCP and Power Design. It does not change the generic SolidWorks tool catalog or claim that a registered tool is production-validated.

## 1. Role and boundary

SolidWorks MCP is the three-dimensional CAD execution and native measurement provider. It owns SolidWorks sessions, model/assembly/drawing operations, COM or other adapter details, native geometry readback, save/reopen behavior, and raw CAD diagnostics.

The transformer workbench owns task identity, design baselines, parameter mappings, twelve engineering checks, cross-tool reconciliation, approval, and release. A successful MCP call is an execution result; it is not an engineering PASS.

The intended path is:

```text
Transformer Desktop App
  -> task/evidence service
  -> transformer SolidWorks adapter
  -> one leased local executor
  -> SolidWorks MCP
  -> SolidWorks application
```

The AI agent should request a small allowlist of business actions. It should not freely select arbitrary low-level tools, VBA, paths, or overwrite operations.

## 2. Capability and identity model

Every session and operation must distinguish:

1. tool registered;
2. code path available;
3. mock adapter executable;
4. MCP process connected;
5. actual SolidWorks version reported;
6. specific operation completed against native SolidWorks;
7. result independently measured and persisted.

`solidworks_get_capabilities` should be called before a transformer run and its result recorded with the task. The capability snapshot should include:

```json
{
  "backend": "mock|pywin32|other",
  "solidworks_version": "...",
  "mcp_version": "...",
  "build_id": "...",
  "process_id": 0,
  "connection_owner": "...",
  "supported_operations": [],
  "operation_validation": {},
  "license_or_session_status": "...",
  "transport_ready": true
}
```

Capability discovery never changes `engineering_status` to PASS. Mock output is simulation evidence only. A COM route or an existing file is not, by itself, native CAD evidence.

## 3. Unified operation envelope

The transformer adapter should wrap the existing SolidWorks operation result without discarding the native response:

```json
{
  "task_id": "...",
  "run_id": "...",
  "baseline_id": "...",
  "operation_id": "...",
  "execution_status": "QUEUED|PROCESSING|SUCCEEDED|FAILED|TIMEOUT_UNKNOWN|CANCEL_REQUESTED|UNKNOWN_NEEDS_RECONCILIATION",
  "engineering_status": "UNKNOWN",
  "evidence_status": "SIMULATION|RUNTIME_REPORTED|VERIFIED_ACTION",
  "backend": "...",
  "solidworks_version": "...",
  "mcp_version": "...",
  "template_hash": "...",
  "input_hash": "...",
  "configuration": "...",
  "documents": [],
  "measurements": [],
  "artifacts": [],
  "warnings": [],
  "errors": [],
  "raw_result_ref": "..."
}
```

Timeout, cancellation, process loss, modal dialogs, or a busy stdio lock must not trigger an automatic replay of a mutating operation. The executor first isolates the session and reconciles the document, artifact, and operation state. If the result cannot be proven, it remains `UNKNOWN_NEEDS_RECONCILIATION`.

## 4. Controlled transformer actions

The first transformer-specific adapter should expose business operations such as:

| Action | Internal SolidWorks work | Required evidence |
|---|---|---|
| `prepare_model_workspace` | capability check, leased session, isolated directories | capability snapshot and input hash |
| `instantiate_approved_template` | copy template, select configuration, apply allowed parameters, rebuild | template/configuration/rebuild report |
| `read_geometry_evidence` | native bounding box, volume, dimensions, topology and references | measurements with units and tolerance |
| `verify_assembly_clearance` | activate assembly, resolve references, run interference/clearance workflow | component/revision list and result details |
| `generate_drawing_and_bom` | controlled views, dimensions, annotations and BOM | drawing references, BOM rows and warnings |
| `save_reopen_verify` | save to isolated output, close, reopen, measure again | artifact hashes and post-reopen measurements |

The adapter must restrict input directories, output directories, document references, feature names, configuration names, and mutable parameters. A generated file must be traceable to the current run and must not silently reuse an earlier result.

## 5. Session, lease and stdio rules

Only one transformer executor may mutate a given SolidWorks session/document at a time. The executor owns a long-lived MCP child process or uses an explicitly supported shared proxy. It must report the owner when `Local\\SolidWorksMCP-stdio` or another process lease is occupied; it must not terminate another client to recover the lock.

The executor persists a task state outside the MCP process: queued, running, cancellation requested, interrupted, reconciled, failed, or completed. SolidWorks process health and MCP transport health are separate signals. Closing the app, losing COM, or receiving a JSON-RPC response does not prove that the document has reached the requested engineering state.

## 6. Validation contract

Every real capability claim needs an operation-specific fixture. The minimum baseline is a 40 x 20 x 10 mm part:

1. create or open the controlled document;
2. perform the operation through MCP protocol;
3. read native bounding box and volume;
4. independently compare 8000 mm³ within the registered tolerance;
5. save to an isolated artifact path;
6. close and reopen;
7. measure again and compare hashes, configuration, references, and values.

The next fixtures are an interference-free assembly and a deliberately interfering assembly, followed by a controlled drawing/BOM fixture. Each fixture records SolidWorks version, adapter build, template hash, units, tolerance, expected result, actual result, and generated artifacts.

Catalog tests, mock adapter tests, and protocol serialization tests prove software behavior only. They do not grant native geometry or engineering validity. Real tests must be explicitly marked and their reports retained.

## 7. Transformer twelve-check mapping

SolidWorks is the primary source of native evidence for checks 6 and 7, and an important source for 9, 10, and 12. It can support checks 3–5 and 8 only when the required models, materials, loads, manufacturing rules, and calculation methods are independently available.

| Check | SolidWorks contribution | Transformer responsibility |
|---|---|---|
| 1 Input/applicability | configuration, properties, document metadata | baseline, standards, missing/conflicting inputs |
| 2 Electrical performance | geometry/material inputs | Power Design and deterministic calculations |
| 3 Insulation | native distances/topology where modeled | insulation rules, paths, calculation and scope |
| 4 Thermal/cooling | geometry/material basis | thermal method and boundary conditions |
| 5 Strength/stability | geometry/assembly basis | loads, material properties and structural analysis |
| 6 Dimensions/interfaces | native measurements, holes, envelopes | datums, tolerances, chains and cross-tool reconciliation |
| 7 Assembly/space | mates, references, interference and clearance | maintenance envelope, omitted objects and conclusion |
| 8 Manufacturability | feature/sheet-metal basis | equipment, weld, process and tooling rules |
| 9 Materials/purchased parts | material, configuration and BOM fields | enterprise item codes, supply and interface checks |
| 10 Drawing/BOM consistency | drawing references and BOM extraction | independent model/drawing/item comparison |
| 11 Inspection/test feasibility | measurable native geometry | instruments, methods, fixtures and test plan |
| 12 Version/closure | document/configuration/artifact metadata | dependency graph, approvals, invalidation and archive |

Unsupported measurement, unresolved references, missing configuration, stale artifact, or unverified drawing association must return UNKNOWN or human review. It must not be converted to not-applicable or PASS.

## 8. Implementation sequence

1. Freeze the capability snapshot and unified operation envelope in the transformer adapter.
2. Complete real native validation for the baseline part, assembly interference pair, and drawing/BOM lifecycle.
3. Verify COM threading, process lease, modal dialog, cancellation, crash recovery, and stdio ownership behavior.
4. Build a controlled `VIRTUAL-S13-M-630/10` template only after the generic fixtures pass; record all assumptions and unmapped parameters.
5. Start with checks 6, 7, 10, and 12 using real SolidWorks evidence; keep the remaining checks simulated or UNKNOWN until Power Design and enterprise rules are available.
6. Expose only the high-level business actions to the transformer agent and retain explicit engineer approval before release.

## 9. Non-goals and current blockers

This project does not define transformer electrical calculations, Power Design semantics, CAXA drawing behavior, enterprise BOM rules, or release approval. Current blockers include real SolidWorks session ownership, operation-specific native validation, product templates, confirmed Power Design fields, and cross-tool parameter/units mapping.

Until those items are completed, the MCP can support controlled simulation and real technical experiments, but it must not be described as an unattended transformer design system or as a source of engineering approval.


## 调用方式选型与部署决策（2026-10-15）

本节为三项目统一的目标设计，尚未实现的执行器、网关和远程服务须按阶段开发验收。六种方式包含进程生命周期、接入接口和部署位置三个维度，可以组合使用；文件队列是 CAXA MCP 内部机制，不意味着本项目应绕过 MCP 直接写队列。

| 方式 | 适用场景 | 优点 | 代价与限制 | 本项目选择 |
|---|---|---|---|---|
| 每任务临时启动 MCP stdio | 原型、离线 mock、低频独立任务 | 实现简单、任务进程边界清晰 | 重复初始化、容易撞锁；结束子进程不保证 CAD 停止；不适合连续会话 | 用于模拟和诊断，不作为真实长作业默认方式 |
| 本地持久工程执行器管理 MCP | 首期真实 CAD 接入 | 复用连接，统一串行调度、租约、证据和恢复 | 需持久任务日志、心跳、重启对账；长期会话需要失效控制 | 首期默认 |
| 本地共享工具网关 | 多客户端共享工作站 | 统一排队、权限、审计和版本 | 需另建 HTTP/命名管道/RPC 服务及客户端隔离；当前 stdio 不是共享端点 | 多用户阶段建设 |
| 原生插件或 SDK 直连 | 深度定制、高性能批量动作 | 原生对象访问充分，可减少协议往返 | 软件版本耦合、开发维护和宿主崩溃风险；可能重复两个 MCP 已有能力 | 有实测瓶颈后局部优化 |
| 离线作业包/批处理 | 审核后出图、夜间批量任务 | 输入固定、便于审计和复现 | 交互反馈慢，仍依赖 CAD 会话和许可证；异常需要对账 | 作为执行器的一种作业模式 |
| 远程工程工作站服务 | 多终端共享 CAD 硬件/许可 | 集中维护版本和工程资源 | 网络失联、桌面会话、许可并发和远程状态确认复杂 | 后续按资源需求建设 |

### 首期推荐架构

```text
桌面 App → 任务/证据服务 → 本地工程执行服务
  ├─ SolidWorks worker → 独占持久 SolidWorks MCP stdio → 适配器/COM → SolidWorks
  └─ CAXA worker → 独占持久 CAXA MCP stdio → 文件队列 → ObjectCRX → CAXA
结果归一化 → 十二类检查 → 人工审批 → 发布
```

工程执行服务管理两个独立 worker；worker 进程、MCP 进程与 CAD 进程的生命周期分别记录。同一受控 CAD 会话内的修改串行执行，跨任务不复用未对账文档。CAXA 优先使用受限批量绘图并逐项回读；SolidWorks 在受控模板重建后执行原生几何测量。交付前两者均执行保存、受控关闭/切换、重开、再次读取。不得为完成重开而关闭用户未保存文档。

stdio 连接由执行器独占。发现已有进程或锁时返回 BUSY 和可核实的归属，不接管其他客户端通道、不强制杀进程。持久连接不等于软件健康；传输、CAD 会话、文档配置和动作后置条件分别检查。超时或取消不等于 CAD 已停止，写操作结果未知时隔离 worker，完成对账后才继续。

### 三工具协作与资料交换

典型三维路径为：Power Design 输出计算结果 → 统一参数包及受控结构派生 → SolidWorks 模型/装配及原生回读 → CAXA 二维图纸及实体/标注回读 → 参数、模型、图纸、BOM 对账 → 十二类适用检查 → 审批发布。Power Design 未提供的结构尺寸必须由批准规则或工程师补齐并记录来源。

该顺序按任务依赖选择，不强制所有产品经过三维建模。批准的二维模板路径可以由统一参数包直接驱动 CAXA；缺三维证据时按实际检查范围登记。SolidWorks 工程图与 CAXA 图纸分别确定权威文件和用途，避免维护两份不受控的同名生产图。跨 CAD 导入/导出仅在格式和对象保真验收通过后启用；CAXA 不默认具备直接读取 SolidWorks 原生模型的能力。

两个 MCP 不互相直接调用。变压器项目传递带版本、单位、坐标系、配置和哈希的资料包，记录任务输入输出及对象映射。独立分支可并行，但不能同时写同一文件；汇总前核对同一设计基线及依赖版本。几何、标注、BOM 和文件存在性分别验收，任一工具真实化不代表整条流程已真实验证。

### 分阶段落实

1. 模拟阶段：临时 mock MCP 或现有模拟适配器验证契约、界面、任务和证据。
2. 真实试验阶段：两个持久独占 worker，先完成能力探测、基准件/基准图、保存重开及故障对账。
3. 产品试点阶段：接入 VIRTUAL-S13-M-630/10，逐项替换真实证据，保留模拟和 UNKNOWN 标识。
4. 多用户阶段：共享网关统一调度和授权，worker 继续独占 CAD 会话。
5. 扩展阶段：依据性能、维护成本和许可验证决定插件优化、离线批处理或远程工作站。

## Multi-user shared SolidWorks workstation

If several users share one SolidWorks license and application instance, place a shared workstation gateway above the MCP process:

```text
Multiple desktop clients -> task queue/gateway -> SolidWorks worker -> one SolidWorks instance
```

Clients must not connect to the same stdio channel or start separate MCP processes that mutate the same SolidWorks session. The gateway owns authentication, project permissions, queueing, priority, fairness, worker lease, document lock, heartbeat, cancellation, audit log, and result reconciliation. The SolidWorks worker is the only writer for its session; read operations also check whether the document is reserved by a write task.

Each task receives an isolated working directory, input snapshot, output path, idempotency key, and lease containing user, task, process, document, start time, deadline, and heartbeat. A client disconnect, COM failure, modal dialog, process crash, or timeout does not release the document lease or replay a mutation automatically. The executor first reconciles the native document and artifacts; unresolved state remains `UNKNOWN_NEEDS_RECONCILIATION`. Release jobs may receive higher priority but cannot forcibly interrupt an active write.

The first shared deployment should use one Windows workstation, one gateway, one SolidWorks worker, and one SolidWorks instance. Add another workstation or license when throughput requires it; do not introduce concurrent writes into one SolidWorks process.
