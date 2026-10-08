# OpsPilot Agent API：DeepSeek 配置

项目默认通过 `langchain-openai` 的 OpenAI 兼容接口使用 DeepSeek `deepseek-flash`。这是 DeepSeek-V4.1-Flash 的 API 模型 ID，不要把展示名称 `DeepSeek-V4.1-Flash` 直接写进 `LLM_MODEL`。模型地址和名称已有默认值，实际调用只缺 API Key。

1. 在项目根目录的 [`.env`](../../.env) 中填写 `LLM_API_KEY=你的DeepSeek_API_Key`。该文件已被 `.gitignore` 忽略；不要把 Key 写进 `.env.example`、源码、测试或聊天消息。
2. 从 `apps/agent-api` 目录启动，让 Uvicorn 加载根目录的 `.env`：

   ```bash
   .venv/bin/uvicorn app.main:app --env-file ../../.env --host 127.0.0.1 --port 8000
   ```

3. 打开 `http://127.0.0.1:8000/api/health` 检查配置状态。Key 留空时 `/api/chat` 返回 `LLM_NOT_CONFIGURED`。Agent API 与 MCP 均需设置 `BUSINESS_BASE_URL`、`INTERNAL_SERVICE_TOKEN`，以读取 Go/MySQL 中的绑定；Agent 还需 `MCP_URL` 和与 MCP 相同的 `MCP_CALL_SECRET`（至少 32 字符）。所有服务的启动命令见根目录 README。

目前 `/api/chat` 使用统一 ReAct：根据当前 Agent 的手动绑定加载已接通工具，模型自主回复、追问或组合工具。聊天前置语义 Router 已移除。每次 Tool 调用在 API 和 MCP 分别读取最新绑定并校验。响应的 `route.kind=agent` 只是运行方式标签，`tool_calls` 是实际工具记录；单次调用兼容返回 `data`。支持可选 `skill_name=crm.customer_insight`，也可由模型调用本地 `activate_skill` 控制启用；同一循环随后收紧为三个工具，不增加角色权限。回访审批仍待实现。`X-Demo-Role` 默认 `consultant`，仅用于本地演示。

`POST /internal/knowledge/search` 要求内部服务令牌和严格 `query/top_k`，使用本地 FastEmbed BGE 中文模型与 Qdrant；不暴露为管理功能、不直连业务表。先按根 README 启动向量库、运行 `index-sop`，再手动绑定知识工具。当前轮次证据账本把真实片段的 `source_id` 传给模型；最终 JSON 的引用身份、原文和轮次由服务端校验，前端只展示服务端提供的元数据。无匹配、依赖失败或伪造引用时不返回成功建议。这个校验不是自然语言语义蕴含证明。

连接接口为 `/api/admin/mcp-endpoints`（列表/新增）、`/{id}`（编辑）、`/{id}/check`（检查）、`/{id}/tools`（已检查目录）；角色接口为 `GET /api/admin/agents`、`PUT /api/admin/agents/{id}/capabilities`。只在本地 development 环境开放。角色保存包含 Endpoint 集合、ToolRef、角色及 Endpoint 预期版本；冲突返回 409。旧无来源的工具写接口返回 `BINDING_SCHEMA_CHANGED`，不猜测来源。

连接以 `role_id` 归属已有角色，不再接受任意领域 `category` 字段。一个角色可绑定多个本角色连接，不能绑定其他角色或未归属连接；运行前和每次 Tool 调用前都再次验证。工具目录需同时匹配部署的 `opspilot/endpointId` 和 `opspilot/roleId`。CRM/MES 只用于工具领域分组；默认连接地址是 `http://127.0.0.1:3100/mcp/consultant`。

模型函数别名由工具名前缀与 ToolRef 稳定散列组成，同名工具不会覆盖；正式 MCP Tool 名仍保留点号。请求签名 v2 绑定角色版本和 Endpoint 身份/修订号。客户端禁用环境代理和重定向，有总超时、分页/响应大小限制；当前仅允许白名单中的明确 IP 地址，容器 DNS/任意第三方服务尚不支持。配置 Profile 只引用服务端环境密钥，不能读取 `LLM_API_KEY` 作 MCP 凭据。模型侧 400 仍转为安全的 `LLM_BAD_REQUEST`。

需要切换模型时，在 `.env` 改 `LLM_MODEL`（例如 `deepseek-v4-pro`）；需要切换兼容服务时再改 `LLM_BASE_URL`。DeepSeek 默认以非思考模式完成当前的 Tool 调用链路，避免额外的推理消息协议要求。应用只在运行时从环境变量读取 Key，不会把它写入源码或日志。
