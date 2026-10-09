# OpsPilot

面向模拟 CRM / MES 的个人开源运营助手。Python + FastAPI + LangChain / LangGraph ReAct，TypeScript MCP，Go + Gin + GORM，MySQL，React + TypeScript。

所有业务数据来自项目的模拟种子及受限回访流程，只含自编样例。应用不连接真实业务系统，不提供通用 SQL、Shell 或任意 HTTP 工具。

## 当前可用能力

- 普通对话和必要的追问；每条消息进入同一个 ReAct 运行时。
- 客户概览、未关闭工单、生产 / 交付工单查询；Agent 自主选择和组合已绑定工具。
- MCP 连接管理：按所属角色筛选、搜索、登记 / 编辑 / 停用、显式检查连接及工具目录。
- 角色工具绑定：先选择 Endpoint，再按来源 / 领域搜索并勾选工具；同名工具可按来源独立绑定。
- MySQL 持久化绑定、配置版本冲突检测、配置变更审计；API 与 MCP 每次执行时重读当前绑定。
- 真实工具调用记录、结构化业务结果、请求 ID 和安全错误展示。
- 自编中文 SOP 检索、当前轮次引用校验；客户洞察 Skill 在同一个 ReAct 中启用并收紧工具集。
- 持久化回访任务：后台 ReAct 生成带 SOP 引用的提案，人工批准后才保存模拟 CRM 计划；支持拒绝、取消、幂等确认和审计。
- 可见执行 Trace：按真实顺序查看用户 / 系统 / 模型消息、工具提议 / 开始 / 结果 / 拒绝，保留并行调用的实际完成顺序；MySQL 保存历史，运行中可查看。

全新数据库初始只有 `consultant`（运营顾问），在默认 `business` Endpoint 下绑定两个 CRM 工具；升级保留已有人工选择。默认路径是 `/mcp/consultant`，Endpoint 明确归属 `consultant`；保留稳定 ID `business` 是为了不改写已有 ToolRef，不是领域分类。一个角色可以拥有多个 Endpoint，但不能绑定其他角色的连接。CRM、MES、知识库、工作流只用于连接内部的工具模块与搜索分组。SOP 检索需手动绑定并建立索引；回访需要在同一角色连接手动绑定两个 CRM、SOP 和 workflow 四个工具。模型只能生成草案，不能自己批准或执行 commit。

旧 `/mcp`、`/mcp/crm`、`/mcp/mes` 不再是默认部署路径。一次性迁移把原默认连接调整为运营顾问连接；旧的未归属或归属冲突连接停用并保留记录，不删除数据、不清空工具选择。自定义连接需维护者明确分配角色并部署对应角色身份，不能仅修改展示名称。现行规格见 [角色 Endpoint 修正](docs/changes/role-endpoints.md)。

Redis、30 条评测集和全栈 Docker Compose 仍是后续 MVP 任务。当前不提供历史会话记忆，也没有实时天气工具。SOP 与客户洞察的当前规格见 [客户洞察增量](docs/changes/customer-insight.md)，任务与审批见 [回访工作流](docs/changes/followup-workflow.md)。本实现称“持久化异步任务编排”，未宣称支持 MCP Tasks。

## 架构

```mermaid
flowchart LR
  UI[React Web] --> A[FastAPI]
  A -->|读取当前配置| G[Go 内部接口]
  G --> DB[(MySQL)]
  A --> R[统一 ReAct]
  R -->|选择已绑定工具| P[API 参数与权限检查]
  P --> EC[按已授权 Endpoint 查找 Client]
  EC --> M[TypeScript MCP 角色路径]
  M -->|独立读取绑定并校验| G
  M -->|模拟业务查询| G
  M -->|SOP 查询 / 内部令牌| S[Agent 内部 RAG]
  S --> E[本地 BGE 中文 Embedding]
  S --> Q[(Qdrant)]
  R --> C[本轮引用校验]
  UI --> TM[连接与工具管理 API]
  TM -->|tools/list| M
  TM -->|版本化保存| G
  R -->|启用回访 Skill| T[持久化任务 API]
  T --> G
  G -->|领取租约| W[后台受限 ReAct Worker]
  W -->|四工具提案 / 批准后 commit| M
  UI -->|人工确认 + 当前版本| T
  R -->|真实边界事件 / 保存前脱敏| TR[Trace 记录器]
  TR -->|固定内部接口| G
  UI -->|本机只读时间线| TA[Trace 查询 API]
  TA --> G
```

Role 保存手动选定的本角色 Endpoint 和 `{endpoint_id, tool_name}` 能力；Tool 是独立业务契约；Skill 只限制现有授权，不授予新工具。简单查询不强制加载 Skill。连接选择不做意图分类，聊天入口没有语义 Router。客户洞察可由快捷操作显式启用，也可由模型单独调用本地编排控制 `activate_skill` 启用；同一 ReAct 后续只暴露三个允许工具。API 与 MCP 都检查所属角色、当前角色版本、Skill 工具范围、来源身份和 Endpoint 执行修订号；目录元数据也核验 `opspilot/roleId`。停用或解绑会拒绝后续旧授权。已经获准并开始的读取不承诺即时取消。连接失效不会改用另一来源的同名工具。

## 目录

| 路径 | 内容 |
| --- | --- |
| `apps/agent-api` | 模型集成、ReAct、MCP Client、工具管理 / Task / Trace API、任务 Worker、调用校验。 |
| `data/sop`、`infra/compose.rag.yaml` | 三份自编 SOP 和仅启动 Qdrant 的开发 Compose。 |
| `apps/mcp-server` | 五个按领域定义的 MCP Tool、Zod、签名和动态绑定校验。 |
| `apps/business-service` | 自编模拟数据、限定查询、Task/审批/审计、回访事务、绑定与 Trace 存储。 |
| `apps/web` | 聊天、快捷操作、工具管理、引用、任务轮询 / 提案 / 人工确认 / 审计、Trace 时间线。 |
| `scripts/dev.py` | 加载根目录 `.env` 后启动单个服务或运行真实联调。 |
| `scripts/smoke-ui.mjs` | 可选的独立 Chrome 页面交互验证。 |
| `requirements.md`、`design.md`、`tasks.md` | 需求、设计和后续实施计划。 |
| `docs/changes/tool-management.md` | 本次工具管理变更的验收标准与任务记录。 |
| `docs/changes/mcp-endpoints-*.md` | Endpoint 需求、设计及当前实施验证记录。 |

## 本地运行

需要 Python 3.12+、Go 1.25+、Node.js 22+、pnpm 和 MySQL 8。首次安装可用 `uv sync --project apps/agent-api` 创建 Python 环境；分别在 `apps/mcp-server`、`apps/web` 执行 `pnpm install --frozen-lockfile`。首次配置复制 `.env.example` 为 `.env`；已有 `.env` 时只补充缺失字段，不覆盖密钥。

| 环境变量 | 用途 |
| --- | --- |
| `LLM_API_KEY` | 模型 Key，仅后端读取。 |
| `LLM_BASE_URL`、`LLM_MODEL` | 默认 `https://api.deepseek.com`、`deepseek-flash`。 |
| `LLM_TIMEOUT_SECONDS` | 模型调用超时，默认 30 秒。 |
| `MCP_URL` | 默认连接的首次迁移地址 `http://127.0.0.1:3100/mcp/consultant`；迁移后实际连接取 MySQL 配置。已知旧默认路径有一次性迁移，自定义路径不自动改写。 |
| `MCP_CALL_SECRET` | Agent 与 MCP 共用的签名密钥，至少 32 字符。 |
| `MCP_ALLOWED_TARGETS` | 可选 JSON URL 数组；未设置时仅允许默认 URL 和本地顾问角色路径。Go 与 Agent 必须一致；首版只支持明确回环 / 私有 IP，不支持 DNS 主机名或重定向。 |
| `MCP_CREDENTIAL_PROFILES` | 可选 JSON Profile 对象：`secret_env`、`endpoint_ids`、`targets`。默认 `local` 引用 `MCP_CALL_SECRET` 并限定 business；只接受该密钥名或 `OPSPILOT_MCP_SECRET_*`。 |
| `MCP_ENDPOINT_DEFINITIONS` | 可选 MCP 部署 JSON 数组：`id/role_id/path/domains`，可选受限 `secret_env`；默认仅运营顾问连接。`domains` 是连接内部可注册的工具模块，不是 Endpoint 分类。登记连接不会启动新服务。 |
| `BUSINESS_BASE_URL` | Agent 和 MCP 访问 Go，通常为 `http://127.0.0.1:8082/`。 |
| `INTERNAL_SERVICE_TOKEN` | Agent、MCP、Go 共用的内部服务令牌，至少 16 字符。 |
| `BUSINESS_DATABASE_DSN` | Go 连接项目专用 MySQL 的 DSN，含 `parseTime=true`。 |
| `BUSINESS_LISTEN_ADDR` | 默认 `127.0.0.1:8082`。 |
| `APP_ENV` | 本地管理页要求 `development`。 |
| `TASK_WORKER_ENABLED` | development 默认 `1`；设为 `0` 只开放 Task API，不自动领取任务，用于串行联调。 |
| `SEED_DATE` | 首次生成模拟数据的日期基准；重新播种可能更新模拟记录。 |
| `QDRANT_URL` | 内部向量库，默认 `http://127.0.0.1:6333`，只接受回环/私有 IP。 |
| `RAG_BASE_URL` | MCP 访问内部检索接口，默认 `http://127.0.0.1:8000/`。 |
| `QDRANT_IMAGE` | 开发 Compose 镜像，默认 `qdrant/qdrant:v1.17.0`；可用下述国内镜像代理。 |

先启动 MySQL。在当前开发机器上可启动已有项目容器：`docker start opspilot-mysql-dev`（端口 `127.0.0.1:3307`）。其他机器请提供自己的项目专用 MySQL 和 DSN。首次初始化业务数据，按业务服务 README 运行 `cmd/seed`；服务启动会自动迁移配置表，初始化时仅创建不存在的默认绑定，重启不会重置选择。

在 MCP 模块执行 `pnpm build` 后，从项目根目录分别在四个终端运行：

```bash
apps/agent-api/.venv/bin/python scripts/dev.py business
apps/agent-api/.venv/bin/python scripts/dev.py mcp
apps/agent-api/.venv/bin/python scripts/dev.py agent
apps/agent-api/.venv/bin/python scripts/dev.py web
```

### 首次启用 SOP

先安装新增 Python 依赖（已有虚拟环境）：`apps/agent-api/.venv/bin/pip install -e apps/agent-api`。从项目根目录启动向量库并索引：

```bash
QDRANT_IMAGE=m.daocloud.io/docker.io/qdrant/qdrant:v1.17.0 docker compose --env-file .env -f infra/compose.rag.yaml up -d
apps/agent-api/.venv/bin/python scripts/dev.py index-sop
```

首次索引下载公开的 BGE 中文模型到 `.cache/fastembed`（已忽略），不使用 LLM Key；聊天请求不会下载模型。若 Hugging Face 不可达，可仅为索引命令加 `HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1`，下载同一公开模型。SOP 修改后重新索引并重启 Agent，以加载当前语料；重复索引幂等，旧 collection 不自动删除。Qdrant 数据保存在专用 Docker 卷中，未使用 `down -v`。

重启 Agent/MCP 后，在管理页检查 `business` 目录，并手动绑定 `knowledge.search_sop`。客户洞察还要求同时绑定两个 CRM 工具。没有这些授权时 Skill 明确拒绝，不自动勾选工具。本 Compose 仅提供 Qdrant，不是全栈一键启动。

每个终端用 Ctrl+C 停止对应服务。打开 [本地工作台](http://127.0.0.1:5173/)，侧栏“MCP 连接”按角色管理服务，“工具管理”配置角色能力。Endpoint ID、所属角色必须与部署身份一致，例如 `business / consultant → http://127.0.0.1:3100/mcp/consultant`；先检查再选择工具。保存无需重启服务；变更服务端环境白名单/密钥/部署定义则须重启对应服务。管理接口只在本机开发环境开放，`X-Demo-Role` 是演示身份，尚未实现生产认证。没有角色创建界面，现有及后续预置角色使用同一套绑定接口。

### 回访任务

先建立 SOP 索引，再在运营顾问的同一个 Endpoint 绑定 `crm.get_customer_overview`、`crm.list_open_tickets`、`knowledge.search_sop`、`workflow.create_followup_plan`。不自动增加任何角色授权。

旧固定种子的续费日期可能已过期，此时任务正确返回“无符合条件客户”。需要当前日期演示时，从根目录运行以下追加命令；它按 Asia/Shanghai 日期新增一位高风险模拟客户与工单，重复运行不覆盖已有数据：

```bash
apps/agent-api/.venv/bin/python scripts/dev.py seed-followup
```

点“回访计划”并发送消息，右侧“持久化回访任务”每 1.5 秒更新。审查候选客户、固定行动、模型分析与 SOP 原文后点“批准并保存”，还须确认对话框；也可拒绝或取消。Task 有效期 24 小时，窗口为 `[今天, 今天+7日]`，包含两端，不是 7×24 小时。当前上限为每人 20 个未完成任务、每任务 20 位候选，超限明确失败。

MySQL 保存 Task、决策与追加审计；Worker 每秒领取一个任务，租约 120 秒、每 30 秒续期，异常重启后可重新领取过期租约，每执行阶段最多领取 3 次。模型/工具错误会记录失败，不无限重试。取消立即撤销服务端执行授权，但不保证立即终止正在进行的模型请求；`committing` 不接受取消，已经提交的事务不回滚。批准后再次核实当前绑定、客户风险/状态/续费日，整批事务写入，重复批准不重复创建记录。生成任务后修改任何角色绑定版本会使任务安全失败，需要重新生成提案。

回访仅保存模拟计划，不发送邮件、拨打电话或联系任何真实客户。当前 Task API 与确认操作仅面向本机 development 演示身份，不是生产认证或防篡改审计平台。

## 演示

1. 发送“你好”：正常回复，不要求客户编号，不调用业务工具。
2. 发送“查询客户 C1001 的基础信息和未关闭工单”：Agent 自主组合两个 CRM 工具，页面显示两次真实调用及模拟结果。
3. 检查运营顾问的 `business` 连接，在“工具管理”的 MES 领域勾选生产状态工具并保存，问“查询 WO-1001 的生产状态”。不需要单独创建 MES Endpoint。
4. 解绑工具或 Endpoint 后，后续调用不再获得授权；停用连接会阻止调用，但保留已选配置。绑定多个同名来源时明确指定来源或先澄清，不声称不同来源代表不同业务数据。
5. 绑定 SOP 后，发送“查询高风险客户续费 SOP”：只用知识工具即可，不需要 Skill。
6. 点“客户洞察”，发送“结合 C1001 的工单、续费风险和 SOP 给出跟进建议”：同一 ReAct 使用三个受限工具，展示 `[1]` 引用和原文卡片。引用只能来自本轮检索；无匹配或检索故障时说明限制，不展示凭空建议。
7. 点“回访计划”，发送“为未来 7 天到期且高风险的客户生成回访计划”：状态从生成提案到等待人工确认；查看 SOP 原文，人工批准后显示正式计划 ID，拒绝/取消则不写正式计划。

查询所需依赖未启动时显示对应错误。Go / MySQL 是当前绑定事实来源，因此绑定服务不可用时聊天也会安全失败，不使用旧权限继续执行。

## 验证与已记录结果

```bash
# 项目根目录
apps/agent-api/.venv/bin/ruff check apps/agent-api scripts/dev.py
apps/agent-api/.venv/bin/pytest apps/agent-api/tests -ra

# 各模块目录
go test ./...                   # apps/business-service
pnpm typecheck && pnpm test      # apps/mcp-server
pnpm typecheck && pnpm test && pnpm build  # apps/web

# Go/MCP/MySQL 已运行后，从根目录执行
apps/agent-api/.venv/bin/python scripts/dev.py verify-bindings
# 再启动 Qdrant、建立索引和启动 Agent 后
apps/agent-api/.venv/bin/python scripts/dev.py verify-insight
# 可选：真实模型联调（使用现有 Key，会产生模型调用费用）
OPSPILOT_TEST_LIVE_MODEL=1 apps/agent-api/.venv/bin/python scripts/dev.py verify-insight
# 回访联调：先用 TASK_WORKER_ENABLED=0 启动 Agent，再串行执行；结束后正常重启 Agent
TASK_WORKER_ENABLED=0 apps/agent-api/.venv/bin/python scripts/dev.py verify-followup
# Trace 联调：固定模型，不调用付费模型、不修改绑定 / CRM，保留测试 Trace
apps/agent-api/.venv/bin/python scripts/dev.py verify-trace
```

2026-10-08 角色 Endpoint 修正的验证记录见 [角色 Endpoint 修正](docs/changes/role-endpoints.md)。此前领域 Endpoint 版本的验证保留在旧任务文档中，不代表当前部署路径。真实模型验证仍以既有记录为准；本轮使用固定响应测试模型验证编排与授权，不产生模型准确率指标。

联调只使用已有的运营顾问连接，短暂修改 `consultant` 的工具绑定并停用/恢复其连接，最后按版本恢复原角色选择；因此只应在本地项目演示库执行。不创建领域 Endpoint，配置版本和审计保留真实测试记录。结果不是 30 条评测集或准确率报告。若已有人工配置不匹配测试连接，测试失败关闭，不替换其地址。

浏览器冒烟需先启动独立 Chrome 调试实例（端口 9225，临时 Profile，不使用日常浏览器资料），执行 `node scripts/smoke-ui.mjs`。可选 `OPSPILOT_SMOKE_INSIGHT=1 node scripts/smoke-ui.mjs` 还会真实调用客户洞察并核验引用卡片；需要两个 CRM 工具已绑定，其临时 SOP 勾选测试后恢复，也会产生模型调用费用。

修改演示配置的联调必须串行运行，期间不要同时在管理页保存配置。2026-10-08 本次记录：Python 77 passed / 3 个默认跳过；MCP 16 passed；Web 9 passed；Go 回归、真实 Qdrant 重复索引、DeepSeek 客户洞察及真实页面引用验证通过。详细过程和首次格式失败记录见 [客户洞察验证](docs/changes/customer-insight.md)，这些结果不构成模型准确率指标。

回访联调使用固定响应模型驱动真实 ReAct、MCP、Go/MySQL、Qdrant 和 HTTP 人工确认；用只读 `cmd/inspect-task` 检查确认前后真实计划数，并保留模拟任务/计划/审计。浏览器验证为 `OPSPILOT_SMOKE_FOLLOWUP=1 node scripts/smoke-followup-ui.mjs`：真实调用模型，自动化测试明确点击一次批准并处理真实确认对话框，只用于项目模拟库，会产生模型调用费用；不设此变量时只验证现有任务展示。它不更改工具绑定，因此四个工具需事先手动绑定。

Task 状态变化、决策和 CRM 提交审计已持久化，普通聊天也有持久化 Trace。引用校验保证来源、原文和轮次一致，不能证明每条自然语言建议都获得充分的语义支持；未宣称准确率。后续完成 Redis 的明确用途、Compose 一键启动和可复现评测。

## 查看执行 Trace

启动 Go、MCP、Agent 和前端后，发送“查询客户 C1001 的基础信息”，点击回复下方“查看本轮 Trace”，或者从侧栏打开“执行 Trace”。可按请求 / Trace ID 搜索，展开系统说明、模型别名、参数和返回内容；刷新深链接仍定位同一请求。运行详情每秒增量读取已保存事件，列表每 3 秒刷新。

Trace ID 标识单次 HTTP 请求；模型步骤区分同一轮的多次模型调用，步骤 + Call ID 对应工具结果。权限校验拒绝的调用显示“未执行”。模型原文与经引用校验的最终展示回复分别保存；回访只关联 Task ID，后台阶段仍查看 Task 审计，不代表后台完整 Trace。

仅 `APP_ENV=development` 开启采集，查询还要求本机来源；仍使用本地演示身份，不能部署为生产鉴权。内容保存前脱敏，不保存隐藏推理或原始异常。单轮最多 256 事件、单事件最多约 16 KB，截断与采集故障明确标记；15 分钟无更新的运行标记中断。不重放工具，不把采集失败当业务失败。当前没有自动清理历史的保留策略，维护者需自行安排备份与容量管理。

实现、验收与复现记录见 [Trace 设计](docs/changes/trace-design.md) 和 [Trace 任务](docs/changes/trace-tasks.md)。独立 Chrome 调试实例启动后可运行 `node scripts/smoke-trace-ui.mjs`；可选 `OPSPILOT_SMOKE_TRACE_LIVE=1` 才发送一次真实模型只读查询验证聊天跳转，会产生模型调用费用，不修改角色绑定。

2026-10-09 Trace 验证：Python 90 passed / 5 个显式联调默认跳过，Web 15 passed，MCP 17 passed，Go 全量回归通过；真实 MCP / MySQL 持久化和独立 Chrome 页面交互、一次 DeepSeek 只读查询跳转通过。详细边界及首次失败修复见上述任务记录；不是模型准确率指标。
