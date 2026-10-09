# OpsPilot 模拟业务服务（开发中）

Go + Gin + GORM。`cmd/seed` 迁移表并写入自编模拟数据；`cmd/server` 提供健康检查、限定查询和 Task/回访事务接口。数据均为 OpsPilot 原创模拟记录，不连接外部系统。

需要 Go 1.25+ 和 MySQL 8。运行时从环境变量读取 `BUSINESS_DATABASE_DSN`（MySQL DSN，需开启 `parseTime=true`）与 `INTERNAL_SERVICE_TOKEN`（至少 16 字节）；可选 `BUSINESS_LISTEN_ADDR`，默认 `127.0.0.1:8082`。种子日期可用 `SEED_DATE=YYYY-MM-DD` 固定；未设置时以当前 UTC 日期为基准。不要把真实数据库密码或令牌提交到仓库。

```bash
cd apps/business-service
go run ./cmd/seed
go run ./cmd/server
go test ./...
```

已实现的内部接口：

- `GET /internal/crm/customers/{customer_code}`
- `GET /internal/crm/customers/{customer_code}/tickets?status=open`
- `GET /internal/mes/work-orders/{work_order_code}`
- `GET /internal/agents`
- `GET /internal/agents/{agent_id}/bindings`
- `PUT /internal/agents/{agent_id}/bindings`（v2：`endpoint_ids`、ToolRef `tools`、`expected_version`、`expected_endpoint_versions`）
- `GET /internal/agents/{agent_id}/authorization?endpoint_id=...`
- `/internal/mcp-endpoints` 与 `/{id}`：连接列表/登记/读取/编辑
- `/{id}/check`、`/{id}/check-result`：已检查目录及按执行修订号条件保存
- `/internal/tasks`：创建/列表；`/{id}`、`/{id}/audit`、`/{id}/decision`：查询/审计/决策
- `/internal/tasks/claim`、`/heartbeat`、`/finish`：后台租约及阶段结果
- `/internal/workflow/authorize`、`/followup`：独立执行证明校验、限定 propose/commit

连接保存在 `mcp_endpoints`，角色–来源–工具存入关联表并有外键约束。Endpoint 的 `role_id` 必须是已有角色；保存绑定和读取执行授权时校验归属，不允许跨角色连接。已有绑定未移除前，不允许把连接转交给其他角色。更新与审计处于同一事务：名称只更新配置版本，角色/URL/凭据/启停同时更新执行修订号。首次升级将旧名称绑定迁移到默认 `business` 来源，保留旧 JSON 与审计；一次性迁移记录防止重启恢复已移除的授权。随后角色连接迁移保留 ToolRef，将已知默认地址调整为 `/mcp/consultant`，其余未归属或冲突连接安全停用，保留旧分类列用于恢复但不作为现行 API 字段。清空有效，版本冲突返回 409。配置接口受内部服务身份保护，模型没有配置写入 Tool；Go 同样检查 `MCP_ALLOWED_TARGETS` 中的明确 IP 目标。

所有内部请求需带服务端提供的 `X-Internal-Service-Token` 和 UUID 格式 `X-Request-ID`。Task、决策和审计持久化在 MySQL；正式计划提交必须满足真实人工批准、当前租约/版本/角色/Endpoint 绑定、有效草稿及客户资格。整个候选名单的计划写入、草稿提交与 Task 完成在同一事务中进行；重复批准不重复写入。

从根目录运行 `apps/agent-api/.venv/bin/python scripts/dev.py seed-followup` 仅追加当天模拟回访客户和工单，重复运行不覆盖旧记录。只读 `go run ./cmd/inspect-task <UUID>` 在此模块目录读取任务状态和实际计划数，需事先设置项目 DSN；不返回租约或审批证明。

真实 MySQL 8.4 的迁移、重复种子和三条只读接口已在独立本地容器上验证。可重复执行的 MySQL 集成测试仅在设置 `OPSPILOT_TEST_MYSQL_DSN` 时运行；它会迁移并写入固定的模拟标识，因此请只指向专用测试库：

```bash
OPSPILOT_TEST_MYSQL_DSN='用户:密码@tcp(127.0.0.1:3307)/opspilot?charset=utf8mb4&parseTime=true&loc=UTC' go test ./... -count=1
```

该本地测试容器名为 `opspilot-mysql-dev`，数据放在独立命名卷 `opspilot-mysql-dev-data`，只绑定 `127.0.0.1:3307`。完整 Docker Compose 一键启动仍未实现，不能据此宣称 C 阶段已完成。
