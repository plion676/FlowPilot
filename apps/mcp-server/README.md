# OpsPilot MCP Server

此模块使用 TypeScript、官方 MCP SDK 1.30.0 和 Zod 4.4.3。`src/domains/` 按 CRM、MES、知识、工作流定义五个原生 Tool；`src/tools/registry.ts` 汇总固定目录与契约元数据；`src/server.ts` 注册 Tool；`src/index.ts` 提供本地 Streamable HTTP 角色路径 `/mcp/consultant` 和 `/health`。不使用 GraphQL，也没有通用 SQL/HTTP Tool。

```bash
cd apps/mcp-server
pnpm install --frozen-lockfile
pnpm typecheck
pnpm build
pnpm test
pnpm start
```

默认监听 `127.0.0.1:3100`，可通过 `MCP_PORT` 修改端口；仅在 Docker 内部网络部署时把 `MCP_HOST` 设为 `0.0.0.0`。未配置 `MCP_CALL_SECRET`（至少 32 字符）、`BUSINESS_BASE_URL` 和 `INTERNAL_SERVICE_TOKEN` 时，执行门禁默认关闭，可列工具但不可调用。配置后每次调用验证短期签名、Zod 参数与契约指纹，再从 Go/MySQL 独立读取当前 Agent 绑定并检查版本和成员。旧静态直调上下文不能绕过动态绑定；解绑后的旧请求返回 `BINDING_CHANGED`。业务数据来自限定 REST 接口。

`knowledge.search_sop` 经受限 REST 适配调用 Agent 内部检索 API，`RAG_BASE_URL` 默认 `http://127.0.0.1:8000/`，传递内部服务令牌；缺索引、Embedding 不可用、向量库故障和无匹配分别处理。目录 `_meta` 提供契约指纹供 Agent 核对，风险仍以各端注册表为准，不能由客户端改写。签名携带 Skill 时，MCP 独立校验 Role 允许 Skill、当前手动绑定与 YAML Skill 工具范围的最小交集，不接受扩大集合。

`workflow.create_followup_plan` 只在回访 Skill 和服务端 `task_grant` 有效时允许。MCP 向 Go 独立核验所属用户/角色、任务状态/版本、Endpoint、有效租约和 commit 的真实人工批准，业务事务再次核验；模型输入不得包含批准布尔值或证明。后台模型侧仅有 propose Schema；人工批准后后端调用 commit，不要求模型决定写入。没有新增业务 Tool。当前是持久化异步任务编排，尚不支持 MCP Tasks。

默认仅部署运营顾问角色连接：`id=business`、`role_id=consultant`、`path=/mcp/consultant`，内部注册全部五个工具契约；角色仍须逐项手动绑定。`MCP_ENDPOINT_DEFINITIONS` 必须填写 `id/role_id/path/domains`，可以在同一进程中部署不同角色路径并复用领域模块，不启动额外进程。角色是连接的归属，`domains` 只是内部注册的 Tool 模块。旧 `/mcp`、`/mcp/crm`、`/mcp/mes` 默认返回 404。

目录 `_meta` 带 `opspilot/endpointId` 与 `opspilot/roleId`。执行只接受 v2 来源签名，先检查调用角色等于部署角色，再读取 Go 的 `/internal/agents/{id}/authorization?endpoint_id=...` 校验当前来源归属、状态、角色版本、Endpoint 修订号和工具集合；旧签名和跨角色/跨来源重放拒绝。Origin 存在且非本机来源时返回 403。各角色目前查询同一套模拟数据，不代表生产数据行级多租户隔离。
