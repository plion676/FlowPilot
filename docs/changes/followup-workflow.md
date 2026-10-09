# 回访异步任务与人工确认

2026-10-09。按用户授权直接实施；需求→设计→小任务记录如下。

## 需求与可测试验收

作为运营顾问，我希望生成未来 7 个自然日（含今天及第 7 日）的高风险客户回访提案，审查客户、原因、建议及 SOP 原文后决定是否保存正式计划。

1. WHEN 用户启动回访 THEN 同一 ReAct 经本地 Skill 控制创建持久化任务；后台 ReAct 只用角色手动授权与工作流 Skill 的四个工具交集，不自动绑定。
2. WHEN 提案生成 THEN 正式 CRM 计划为零；无候选时完成为空，有候选才等待审批；引用由本轮证据校验提供。
3. WHEN 人工批准 THEN MCP 和 Go 必须重新校验角色绑定、任务、租约、审批和客户资格；重复批准/重试不重复创建计划。
4. WHEN 拒绝、取消或过期 THEN 不再创建正式计划；取消不能伪装成已中止一个已经提交的事务。
5. WHEN 服务重启 THEN MySQL 中的任务与审计仍可查询，未完成任务可在租约过期后恢复；权限撤销、非法跳转、过期租约均失败关闭。
6. WHEN 查看页面 THEN 可轮询任务、审查提案、人工批准/拒绝/取消与读取审计。模拟身份只在本机 development 环境开放，不是生产认证。

边界：仅模拟数据、不真实联系客户、不新增业务 Tool、不引入队列或额外微服务。未经完整官方 MCP Tasks 查询/取消/确认兼容性验证，统一称“持久化异步任务编排”，不宣称 MCP Tasks。

## 设计

统一聊天 ReAct → activate_skill(crm.followup_workflow) → Task API/服务 → Go/MySQL 持久化 → Agent 进程内单 Worker 轮询并领取租约 → 受限 ReAct 提案 → 人工确认 → Worker 经 MCP commit → Go 原子事务创建 CRM 计划并完成任务。

状态：created → gathering → pending_approval / completed_empty / failed；pending_approval → approved / rejected / cancelled / expired；approved → committing / cancelled；committing → completed / failed。created/gathering 可取消；终态不再审批。租约过期的执行阶段可重新领取，最多三次；审批有效期 24 小时。

存储选择：复用现有 Go/GORM/MySQL 管理任务、决策、审计与业务草稿；Python 负责 Agent 编排，通过受服务令牌保护的固定内部接口操作 Repository。这调整了原设计“Python 自建任务 ORM”的实现方式，使最终审批状态、客户重校验、计划写入和任务完成位于同一数据库事务，避免跨 ORM/连接提交竞态。没有新服务，也不允许 Agent 直接查业务表。MySQL 是任务事实来源，轮询不是消息队列；Redis 留给后续明确缓存用途，不承载审批权限。

创建窗口由服务器按 Asia/Shanghai 当日确定，客户端/模型不能覆盖。创建/决策具幂等键；任务存所属角色、用户、Endpoint 与绑定版本。执行租约使用不可猜测令牌，只存哈希，凭证仅在服务间传递，不给模型、前端或审计。MCP 签名包含 task_grant 并独立查询 Go 验证；Go 的业务 propose/commit 事务再次检查。提交参数仍只有 operation/task_id，模型不能提供审批事实。

提案候选和固定回访行动从 Go 模拟业务逻辑生成；Agent 查询候选概览、工单、SOP 并附带核验的引用。提案在待审批后不可修改。批准时重新核实风险、状态、续费日及当前绑定，不扩大到未批准的新客户。正式计划使用 task_id/customer_id 唯一约束；整个名单一次事务写入。

错误：INVALID_ARGUMENTS、TASK_CONFLICT、TASK_EXPIRED、APPROVAL_REQUIRED、BINDING_CHANGED、LEASE_LOST、CUSTOMER_CHANGED、DEPENDENCY_UNAVAILABLE。所有决策绑定当前版本、角色和用户；仅确认接口可产生人工批准。

## 实施小任务

- [x] F1（2–4h）Task 模型、Repository/状态/幂等/租约/审计与 Go 测试；依赖既有 MySQL。验收：持久化、终态、撤权、过期、非法跳转测试。
- [x] F2（2–4h）Go 回访 propose/commit、MCP task_grant 双侧校验与测试；依赖 F1。验收：审批前零计划、批准后唯一写入、客户变化/撤权/伪造证明拒绝。
- [x] F3（2–4h）Python Task Client、受限 ReAct、后台恢复 Worker、HTTP/聊天 Skill 接入与测试；依赖 F2。验收：同一 ReAct 入口、最小工具集、故障可查、引用不编造。
- [x] F4（2–4h）React 轮询/提案/确认/审计、单元/真实联调、文档；依赖 F3。验收：确认、拒绝、取消、无候选四类场景、重复确认、恢复机制测试，不自动改变原绑定。

每项先测试再进入下一项；实际结果在验证后追加，不生成准确率指标。

## API 与可复现验证

`POST /api/tasks` 输入 `idempotency_key`；`GET /api/tasks` / `/{id}` / `/{id}/audit`；`POST /api/tasks/{id}/decision` 输入 `decision=approve|reject|cancel`、整数 `expected_version`、UUID `idempotency_key`。身份来自本机开发上下文，不接受调用者通过请求体指定角色、批准人或执行证明。正式写入不通过聊天消息确认。

四个工具必须在同一角色 Endpoint 手动绑定；`scripts/dev.py seed-followup` 只追加日期标记的模拟客户/工单，不覆盖既有记录。根 README 有完整启动、SOP 索引及验证命令。

2026-10-09 实际验证记录：

- Python 回归 83 passed / 4 个默认跳过（真实依赖用例需显式运行）；Ruff 通过。
- MCP 17 passed，官方 SDK 的 Streamable HTTP 测试、TypeScript 检查与构建通过。
- Web 10 个组件测试、TypeScript 检查与生产构建；Go seed/store/transport 全回归通过，使用内存 SQLite 测试固定时钟和事务边界。
- `TASK_WORKER_ENABLED=0 scripts/dev.py verify-followup`：固定模型驱动真实 ReAct、官方 MCP Client、TypeScript MCP、Go/MySQL、Qdrant、HTTP 确认。直接读取真实 MySQL，审批前计划数 0，批准后 1，重复批准仍 1；取消的任务仍 0，旧租约续期失败；临时绑定最终 CAS 恢复原选择。
- `OPSPILOT_SMOKE_FOLLOWUP=1 node scripts/smoke-followup-ui.mjs`：真实配置的 DeepSeek 通过聊天 ReAct 创建 Task、后台受限 ReAct 检索客户/工单/SOP、页面提案与引用、真实确认对话框、提交及审计全部跑通。任务 `2016da25-b1c0-4f1e-9eac-fd5356bb2df2` 完成，保存 1 份模拟计划。桌面 1440 和手机 390 宽布局无横向溢出、无 JS 异常；截图保存在本机 `/tmp/opspilot-followup-*.png`，不作为跨机器必需文件。
- 持久化结果经过 Agent/Go 进程重启仍可读取。租约过期重新领取、令牌轮换和最大重试的恢复机制以受控时钟单元测试覆盖；未将其描述为真实机器故障压测。拒绝、空候选、客户变化、撤权和过期路径主要由隔离测试验证，不虚称全部进行了真实模型浏览器测试。

首次 MySQL 联调暴露零时间戳不满足严格模式，已用合法的初始非有效租约时间修正；JSON 空结果标志改为反序列化判断，不依赖数据库格式化空格。首次真实模型后台运行失败：联合输入 Schema 不适合模型函数适配；现在模型只获得 `propose` 的对象 Schema，commit 由批准后的确定性 Worker 调用，假模型测试也执行正式函数 Schema 转换。失败任务/审计保留，不删除或伪装成功。

验证向模拟库追加了当天客户、工单、任务和计划，保留审计。未触碰原客户日期、真实业务或密钥。用户原角色工具选择未被默认授权替换。

上限：24h TTL，120s 执行租约 / 30s 续期，每执行阶段最多 3 次领取，最多 20 位候选与 46 次工具调用；模型、工具或引用校验失败直接记录失败，可由用户重新发起。任意绑定版本变化会使旧 Task 失败关闭。取消不承诺立即停掉已发出的 LLM 请求，提交中的事务不可取消。

MCP Tasks 官方 SDK 示例已定位但未运行完整 `tasks/get`、轮询、取消/确认兼容性门槛，因此没有采用该协议或名称。参考 [官方 Tasks 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)。Redis 缓存、30 条评测、全栈 Compose 和生产认证仍是后续任务，不在本次完成范围。
