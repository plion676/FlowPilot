# OpsPilot Web（当前可用切片）

这是 React + TypeScript + Vite 前端。聊天展示普通回复、真实 Tool 调用、客户/工单/MES 结果和安全错误。侧栏“工具管理”支持 MCP 目录、领域筛选、模糊搜索、勾选绑定、参数查看与版本化保存；保存后无需重启。客户洞察快捷入口传递可选 Skill，支持退出洞察模式；回访计划仍显示“尚未接通”。引用卡片展示后端核验过的编号、文档、版本、来源和原文，Task 面板只展示后端数据。

“MCP 连接”支持分类搜索、登记/编辑/停用和显式连接检查；“工具管理”先为角色选 Endpoint，再选择其中 Tool。工具卡片和结果保留来源，同名工具独立勾选。移除来源会提示取消相关工具；管理页离开前对未保存修改要求确认。密钥不进前端，界面只展示凭据 Profile 引用。当前没有角色创建界面或生产登录。

本地启动：

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm dev
```

打开 `http://127.0.0.1:5173/`。Vite 把 `/api/*` 代理到 `http://127.0.0.1:8000`，因此需要另行启动 Agent API；只查看界面时，右侧会显示 API 未连接。真实查询还需要 DeepSeek Key、MCP Server、Go 服务与模拟 MySQL，配置说明见 `apps/agent-api/README.md` 和根目录 `.env.example`。前端不读取、保存或发送 LLM API Key。

验证：

```bash
pnpm typecheck
pnpm test
pnpm build
```

测试覆盖客户洞察显式 Skill 请求、可用快捷操作、请求/角色、错误、引用/Task 数据展示，以及工具搜索、领域筛选、绑定保存和版本冲突。真实浏览器冒烟另覆盖保存/恢复配置、目录读取与手机布局。SOP 查询需启动 Qdrant、首次索引并手动绑定知识工具；步骤见根 README。Task API、状态轮询与人工确认仍待实现。
