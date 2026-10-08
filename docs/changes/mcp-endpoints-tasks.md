# MCP Endpoint 实施任务

> 下列是旧领域 Endpoint 版本的实施及验证记录。现行修正见 [角色 Endpoint 修正](role-endpoints.md)，不再默认部署 CRM/MES 路径。

2026-10-08：用户授权直接实施；依据已确认需求与 Endpoint 设计。

- [x] E1（2–4h）：Go Endpoint/角色关系、迁移、版本与审计。涉及 `store`、`transport`；验收：迁移不覆盖、来源约束、冲突、清空和停用测试。依赖：现有数据库。
- [x] E2（2–4h）：MCP 多路径与来源签名。涉及 TS `endpoints/server/security/backend`；验收：目录隔离、身份重放拒绝、实时撤权。依赖：E1。
- [x] E3（2–4h）：Python 连接管理与 ToolRef ReAct。涉及 `clients/services/api/models`；验收：白名单、同名工具隔离、多工具与零调用。依赖：E1、E2。
- [x] E4（2–4h）：TS 前端连接/角色绑定管理。涉及 `EndpointManager/ToolManager/api/App`；验收：分类搜索、保存检查、来源选择与冲突提示。依赖：E3。
- [x] E5（2–4h）：真实模拟服务联调与文档。涉及集成测试、README、环境样例；验收：真实 MCP→Go→MySQL、跨来源拒绝、配置恢复。依赖：E4。

实现收敛：首版仅连接精确允许的 IP URL；容器 DNS 暂不支持。无 DNS 重绑定降级放行。

## 实际验证记录（2026-10-08）

- Go：`GOCACHE=/tmp/opspilot-go-build go test ./...` 通过；覆盖两角色来源隔离、旧空集迁移、启动不覆盖、版本冲突、审计、检查结果过期和复合外键方向。真实 MySQL 增量迁移通过，不重建数据库。
- MCP：`pnpm typecheck`、`pnpm build`、`pnpm test` 通过，13 项测试。真实 HTTP 目录为 business=5、crm=2、mes=1；来源身份、v2、修订号、停用和 Origin 检查均覆盖。
- Python：Ruff 通过，`pytest -ra` 为 60 通过、1 项 opt-in 联调默认跳过；联调另通过 `scripts/dev.py verify-bindings` 单独执行。
- 联调：真实 MCP→Go→MySQL，以固定响应测试模型驱动 ReAct，分别执行 business/crm 的同名客户查询和 MES 查询；证明 Client 跨来源拒绝、绕过 API 后 MCP 仍拒绝停用/旧修订号；清空后问候零调用；最终 CAS 恢复原角色 Endpoint/ToolRef 选择。固定响应模型不是模型准确率评测。
- Web：类型检查、6 项测试和构建通过；`node scripts/smoke-ui.mjs` 对隔离 Chrome 真实检查分类目录、绑定保存/恢复、搜索和两种页面的桌面/手机布局，无 JS 异常、无横向溢出。
- DeepSeek：真实 `deepseek-flash` 用新的来源别名完成 C1001 基础信息与未关闭工单两次调用，响应包含实际模拟业务结果及 `endpoint_id=business`。无天气能力，不记录天气指标。

本机 CRM/MES 演示连接由联调登记并保留，角色不会自动绑定它们。原角色人工选择（包括已选但未接通的工作流 Tool）保留；配置版本和测试审计真实增加，未回滚审计。测试失败不会重置成默认工具。

尚未增加角色创建/登录、容器 DNS、第三方通用 MCP、RAG、审批或 MCP Tasks。完整执行审计仍为安全结构化日志；配置变更审计持久化于 MySQL。
