package transport

import (
	"encoding/json"
	"errors"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

func taskView(t store.AgentTask) gin.H {
	return gin.H{"task_id": t.ID, "status": t.Status, "version": t.Version, "skill_name": store.FollowupSkill, "endpoint_id": t.EndpointID, "window_start": t.WindowStart, "window_end": t.WindowEnd, "proposal": json.RawMessage(t.ProposalJSON), "result": json.RawMessage(t.ResultJSON), "error_code": t.ErrorCode, "expires_at": t.ExpiresAt, "created_at": t.CreatedAt, "updated_at": t.UpdatedAt}
}
func taskFailure(c *gin.Context, err error) {
	var code store.TaskError
	if errors.As(err, &code) {
		status := 409
		switch string(code) {
		case "NOT_FOUND":
			status = 404
		case "INVALID_ARGUMENTS":
			status = 400
		case "FORBIDDEN_TOOL", "FORBIDDEN_SKILL", "APPROVAL_REQUIRED", "BINDING_CHANGED", "LEASE_LOST":
			status = 403
		}
		fail(c, status, string(code), "任务操作未通过状态、权限或有效期校验")
		return
	}
	if errors.Is(err, gorm.ErrRecordNotFound) {
		fail(c, 404, "NOT_FOUND", "任务或草稿不存在")
		return
	}
	fail(c, 503, "DEPENDENCY_UNAVAILABLE", "任务存储服务暂不可用")
}
func taskIdentity(c *gin.Context) store.TaskIdentity {
	return store.TaskIdentity{Role: c.Query("role"), ActorID: c.Query("actor_id")}
}
func validIdentity(id store.TaskIdentity) bool {
	return agentIDPattern.MatchString(id.Role) && len(id.ActorID) > 0 && len(id.ActorID) <= 80
}
func isUUID(value string) bool { _, err := uuid.Parse(value); return err == nil }

func decodeTask(c *gin.Context, value any) bool {
	if !decodeConfig(c, value) {
		fail(c, 400, "INVALID_ARGUMENTS", "任务参数格式无效或超过大小限制")
		return false
	}
	return true
}

func registerTaskRoutes(internal *gin.RouterGroup, db *gorm.DB) {
	repo := store.NewTaskRepository(db)
	internal.POST("/workflow/followup", func(c *gin.Context) {
		var input struct {
			store.TaskIdentity
			Grant    store.TaskGrant         `json:"grant"`
			Endpoint string                  `json:"endpoint_id"`
			Input    store.FollowupOperation `json:"input"`
		}
		if !decodeTask(c, &input) {
			return
		}
		if input.Grant.TaskID != input.Input.TaskID {
			fail(c, 400, "INVALID_ARGUMENTS", "工作流参数无效")
			return
		}
		var result map[string]any
		var err error
		if input.Input.Operation == "propose" {
			result, err = repo.Propose(input.Grant, input.TaskIdentity, input.Endpoint, input.Input, c.GetHeader("X-Request-ID"))
		} else if input.Input.Operation == "commit" {
			result, err = repo.Commit(input.Grant, input.TaskIdentity, input.Endpoint, c.GetHeader("X-Request-ID"))
		} else {
			err = store.TaskError("INVALID_ARGUMENTS")
		}
		if err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, result)
	})
	internal.GET("/tasks", func(c *gin.Context) {
		owner := taskIdentity(c)
		if !validIdentity(owner) {
			fail(c, 400, "INVALID_ARGUMENTS", "身份参数无效")
			return
		}
		tasks, err := repo.List(owner)
		if err != nil {
			taskFailure(c, err)
			return
		}
		items := []gin.H{}
		for _, task := range tasks {
			items = append(items, taskView(task))
		}
		c.JSON(200, gin.H{"tasks": items})
	})
	internal.POST("/tasks", func(c *gin.Context) {
		var input struct {
			store.TaskIdentity
			Key      string `json:"idempotency_key"`
			Endpoint string `json:"endpoint_id"`
			Version  uint64 `json:"binding_version"`
		}
		if !decodeTask(c, &input) {
			return
		}
		if !validIdentity(input.TaskIdentity) || !isUUID(input.Key) || input.Version == 0 {
			fail(c, 400, "INVALID_ARGUMENTS", "任务参数无效")
			return
		}
		task, err := repo.Create(input.TaskIdentity, input.Key, input.Endpoint, input.Version, c.GetHeader("X-Request-ID"))
		if err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(201, taskView(task))
	})
	internal.GET("/tasks/:task_id", func(c *gin.Context) {
		task, err := repo.Get(c.Param("task_id"), taskIdentity(c))
		if err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, taskView(task))
	})
	internal.GET("/tasks/:task_id/audit", func(c *gin.Context) {
		task, err := repo.Get(c.Param("task_id"), taskIdentity(c))
		if err != nil {
			taskFailure(c, err)
			return
		}
		events := []store.TaskAudit{}
		if err := db.Where("task_id = ?", task.ID).Order("created_at, version, id").Limit(100).Find(&events).Error; err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"events": events})
	})
	internal.POST("/tasks/:task_id/decision", func(c *gin.Context) {
		var input struct {
			store.TaskIdentity
			Key      string `json:"idempotency_key"`
			Decision string `json:"decision"`
			Version  uint64 `json:"expected_version"`
		}
		if !decodeTask(c, &input) {
			return
		}
		if !validIdentity(input.TaskIdentity) || !isUUID(input.Key) || input.Version == 0 {
			fail(c, 400, "INVALID_ARGUMENTS", "确认参数无效")
			return
		}
		task, err := repo.Decide(c.Param("task_id"), input.TaskIdentity, input.Key, input.Decision, input.Version, c.GetHeader("X-Request-ID"))
		if err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, taskView(task))
	})
	internal.POST("/tasks/claim", func(c *gin.Context) {
		task, grant, err := repo.Claim(c.GetHeader("X-Request-ID"))
		if err != nil {
			taskFailure(c, err)
			return
		}
		if task == nil {
			c.JSON(200, gin.H{"task": nil})
			return
		}
		view := taskView(*task)
		view["role"] = task.Role
		view["actor_id"] = task.ActorID
		c.JSON(200, gin.H{"task": view, "grant": grant})
	})
	internal.POST("/tasks/heartbeat", func(c *gin.Context) {
		var grant store.TaskGrant
		if !decodeTask(c, &grant) {
			return
		}
		if err := repo.Heartbeat(grant); err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"ok": true})
	})
	internal.POST("/tasks/finish", func(c *gin.Context) {
		var input struct {
			Grant     store.TaskGrant `json:"grant"`
			Proposal  json.RawMessage `json:"proposal"`
			ErrorCode string          `json:"error_code"`
		}
		if !decodeTask(c, &input) {
			return
		}
		if len(input.ErrorCode) > 80 {
			fail(c, 400, "INVALID_ARGUMENTS", "任务结果无效")
			return
		}
		if err := repo.Finish(input.Grant, input.Proposal, input.ErrorCode, c.GetHeader("X-Request-ID")); err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"ok": true})
	})
	internal.POST("/workflow/authorize", func(c *gin.Context) {
		var input struct {
			store.TaskIdentity
			Grant     store.TaskGrant `json:"grant"`
			Endpoint  string          `json:"endpoint_id"`
			Operation string          `json:"operation"`
		}
		if !decodeTask(c, &input) {
			return
		}
		if err := repo.Authorize(input.Grant, input.TaskIdentity, input.Endpoint, input.Operation); err != nil {
			taskFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"ok": true})
	})
}
