# SOP RAG 与客户洞察

2026-10-08，当前增量。直接实施授权已确认；按需求、设计、任务顺序记录，不扩大五个业务工具范围。

## 需求与验收

1. 可独立调用 `knowledge.search_sop`，不强制启用 Skill。结果必须包含文档、片段编号和原文。
2. 客户洞察在统一 ReAct 中启用 `crm.customer_insight`，查询同一客户概览、未关闭工单及相关 SOP，区分事实、限制和建议。
3. Skill 只限制角色已绑定工具，不授予权限；简单查询/问候仍不需要 Skill。没有三个必需工具时拒绝启用。
4. 引用只能来自本轮真实检索、匹配当前源文档的片段；伪造来源、替换原文、引用旧轮次或无效引用 JSON 不展示成功建议。
5. SOP 无匹配或检索不可用时说明限制，保留已查询事实，不编造赔付、折扣、写入或外发成功。
6. 原有角色 Endpoint、工具手动绑定、实时撤权和人工确认约束不变。工作流写入仍未接通。

## 设计

```text
聊天 / 可选 skill_name
  → 当前角色绑定 → 同一个 LangChain / LangGraph ReAct
  → 模型可自行 activate_skill（本地编排控制，不是新增业务 MCP Tool）
  → Role 已绑定 ToolRef ∩ Skill allowed_tools ∩ 已接通只读工具
  → MCP 再校验当前绑定与 Skill 限制
  → knowledge.search_sop → Agent 内部受服务令牌保护的检索接口
  → 本地 Embedding → Qdrant → 当前源文件精确片段校验
  → 本轮证据账本 → 引用校验 → 回答、引用卡片、实际工具记录
```

Skill 定义和 Role 的 allowed_skills 读取版本化 YAML；工具授权继续以 MySQL 手动绑定为事实来源。Skill 启用后每次模型调用实际只暴露三个受限工具；执行前再次检查，并在 MCP v2 签名中传递 skill_name 和本来源最小工具集。启用控制必须独立调用，不能与业务工具同批。

SOP 在 `data/sop`，全部自编。按 Markdown 小节切分，单片段最多 700 字符；标识由文档 ID、版本与内容摘要生成。Collection 名绑定语料摘要、模型 ID 和切分版本，变更不会继续读取旧索引。重复索引仅幂等写入同一组点；旧版本 collection 不自动删除。模型权重缓存在被忽略的 `.cache/fastembed`，仅索引命令允许下载，Web 请求只使用本地缓存。

Qdrant 固定 v1.17.0，开发 Compose 仅启动这个依赖，不宣称全栈一键启动已完成。部署绑定宿主回环地址。REST Client 禁用环境代理/重定向、设置超时；内部检索 API 严格校验参数和内部令牌。没有新增服务进程。

Embedding 从原设计的 E5 调整为 FastEmbed 内置 `BAAI/bge-small-zh-v1.5`，512 维，面向此阶段中文 SOP，避免额外 ONNX 模型注册。依据：[FastEmbed 官方支持列表](https://qdrant.github.io/fastembed/examples/Supported_Models/)。使用 [Qdrant Query API](https://api.qdrant.tech/api-reference/search/query-points/)，当前阈值 0.55 是固定工程默认值，不是经过准确率评测的最优参数。

引用 JSON 只让模型选择本轮 `source_id`；标题、版本、原文和来源 Endpoint 由服务器提供，不接受模型编造这些字段。回答中的 `[[source_id]]` 与 sources 集合必须一致，输出展示为 `[1]` 等编号。该校验保证引用身份、原文和轮次一致，**不保证自然语言每一条建议在语义上都被引用充分支持**；后续评测仍需检查语义依据。

每次模型调用前，若已有证据，会追加服务端生成的当前 source_id 列表与 JSON 格式提醒。它只是输出指令，不改变权限或降低引用校验；模型仍可能不遵守，失败时丢弃建议并记录安全事件。

## 实施任务与结果

- [x] I1（2–4h）：自编 SOP、切分/标识、索引和检索；`data/sop`、`app/rag`、`infra/compose.rag.yaml`。验收：原文可定位、索引幂等、缺索引/无匹配/依赖故障区分。依赖：既有项目配置。
- [x] I2（2–4h）：内部检索 API、MCP 适配、当前轮次引用账本；`app/api/rag.py`、`src/backend.ts`、`app/agent/citations.py`。验收：身份/参数校验、无旧轮次或伪造引用。依赖：I1。
- [x] I3（2–4h）：Skill Registry 与统一 ReAct 启用、最小工具集合、双侧权限；`app/agent/skills.py`、`services/chat.py`、`services/tool_management.py`、`src/security.ts`。验收：显式/模型自主启用，同一循环继续执行，越权拒绝，不自动绑定工具。依赖：I2。
- [x] I4（2–4h）：Web 快捷操作、引用、单元/真实集成测试与启动文档；`apps/web`、`tests/test_rag.py`、`tests/test_insight.py`、真实联调脚本、README。验收：模拟查询→真实 MCP/Qdrant→可验证引用，原绑定测试后恢复，所有实际结果可复现。依赖：I3。

## 2026-10-08 实际验证

- Python：77 passed、3 skipped（默认跳过三个需真实服务/模型的用例）；Ruff 通过。
- TypeScript MCP：类型检查、构建通过，16 个测试通过；其中 HTTP 测试需允许监听本地随机端口。
- Web：类型检查、生产构建、9 个测试通过；引用版本/来源和显式 Skill 请求有断言。
- Go：`go test ./...` 通过，三个已有测试包使用缓存结果。
- 真实索引：三份 SOP、11 个片段；重复索引仍为 `opspilot_sop_29682b7aa9d2442e8dcf`，语料摘要 `3373262442707e8e7be87fd7cef45f35fdae507c028edc53a132b4df270847af`。BGE ONNX 权重 SHA-256 为 `1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38`，已与公开下载元数据核对；权重不提交仓库。
- `scripts/dev.py verify-insight`：固定模型响应 + 真实 MCP、Go/MySQL、Embedding/Qdrant 检索、当前片段引用成功；MCP 拒绝扩大的 Skill 工具集，撤权后旧签名拒绝。
- `OPSPILOT_TEST_LIVE_MODEL=1 ... verify-insight`：固定模型与真实 DeepSeek 两个用例通过。首次真实模型尝试曾未遵守引用格式，被安全拦下；后续成功不代表模型格式可靠率或回答准确率。已增加当前 source_id 格式提醒，保留严格校验。
- `scripts/dev.py verify-bindings`：原有真实多工具查询、跨角色拒绝、停用/恢复和旧授权撤销回归通过。
- `OPSPILOT_SMOKE_INSIGHT=1 node scripts/smoke-ui.mjs`：真实前端→DeepSeek→MCP→Qdrant→带编号/原文的引用卡片成功；桌面/手机布局、绑定保存/恢复与无页面 JS 异常断言通过。测试初次 CDP 等待条件的 DOM 序列化错误已修复后重跑。截图仅含模拟业务数据。

各联调测试会改动本地演示配置，应**串行运行**，不要同时修改管理页。测试最终恢复任务开始时四项人工工具选择（两个 CRM、MES、工作流；不新增 SOP 授权），版本与真实审计继续递增。维护者需手动启用知识工具后使用洞察。

以上是有限用例的实际结果，不是 30 条评测集、准确率、吞吐量或生产可用性报告。单元测试的假 Embedding / 假 Qdrant 仅验证控制逻辑；语义充分性、阈值优化、Task/Redis/全栈 Compose 仍待后续任务。
