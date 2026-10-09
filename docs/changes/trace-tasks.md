# Trace 实施任务

用户已确认需求并授权直接完成；按以下顺序实施，每项验证后继续。

- [x] T1（2–4 小时）：Go Trace 模型、事务写入与内部接口；涉及 internal/store/traces.go、internal/transport/traces.go、database.go、router.go。验收：重启仍可读、事件幂等、所有权隔离、分页与终态测试通过。无依赖。
- [x] T2（2–4 小时）：Python 脱敏记录器与 ReAct middleware；涉及 app/traces/、agent/react.py、services/chat.py。验收：普通消息、并行工具、拒绝和失败按真实顺序记录，密钥不落库。依赖 T1。
- [x] T3（2–4 小时）：聊天 Trace 生命周期和本地查询 API；涉及 api/chat.py、api/traces.py、main.py、models/chat.py。验收：成功/失败可定位、增量读取、外网/非开发环境拒绝访问、采集故障不改变业务结果。依赖 T2。
- [x] T4（2–4 小时）：React Trace 列表和时间线；涉及 TracePage.tsx、TracePage.css、api.ts、App.tsx。验收：刷新保留选择、运行中更新、消息/参数/结果可展开、错误和截断明确。依赖 T3。
- [x] T5（2–4 小时）：端到端验证与 README；涉及测试文件、README、以上文档。验收：实际服务运行、数据库历史可读、前后端测试及构建通过，真实记录验证结果和已知边界。依赖 T4。

## 2026-10-09 验证记录

- Python：90 passed / 5 skipped（跳过项均为显式启用的真实服务 / 付费模型测试）；Ruff 全量检查通过。
- Web：15 passed，TypeScript、生产构建、Prettier 检查通过。
- Go：go test ./... 通过，包含新增的 Trace 幂等、所有权、分页、终态、256 事件上限及中断测试。
- MCP：17 passed，TypeScript 检查通过；第一次沙箱内 HTTP 测试因不能监听端口失败，授权本机监听后重跑通过。未修改 MCP 业务能力。
- verify-trace：固定模型驱动实际 MCP / Go / MySQL，通过问候、C1001 只读查询、参数拒绝与不存在客户四个场景。新 API 实例可读历史；绑定版本保持 54，没有修改任何绑定或 CRM。四条持久化 Trace：08fbd8ad-6685-482c-b65f-19cad766d08e、8e61e687-ea30-4b23-a25f-2742d3e3d0e0、faac2674-4390-4aa2-9608-b5fd315cc29c、cd7ea637-7da9-4930-914c-91957c649908。允许保留这些模拟测试记录。
- 浏览器：独立临时 Chrome 下，真实页面时间线、空模型消息、来源 / 修订、刷新恢复、390px 移动端无横向溢出，以及一次真实 DeepSeek 只读查询后的聊天 Trace 跳转通过；未发现浏览器运行时异常。
- 首次浏览器检查发现仅 hash 导航不更新选中记录，已补 hashchange 并新增回归测试。内置浏览器自动化因运行环境异常不可用；Chrome 截图接口超时，因此只报告实际 DOM / 交互验证，不报告截图视觉验收。

以上是功能与测试结果，不是准确率、性能或 30 条 Agent 评测报告。隐藏推理、完整后台 Task Trace、自动历史保留策略及生产身份鉴权不在本次实现范围。
