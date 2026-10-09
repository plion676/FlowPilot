package transport

import (
	"encoding/json"
	"errors"
	"regexp"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

type traceIdentity struct {
	Role    string `json:"role"`
	ActorID string `json:"actor_id"`
}

func (v traceIdentity) valid() bool {
	return regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}$`).MatchString(v.Role) && len(v.ActorID) > 0 && len(v.ActorID) <= 80
}
func traceInput(c *gin.Context, value any) bool {
	if !decodeConfig(c, value) {
		fail(c, 400, "INVALID_ARGUMENTS", "Trace 参数无效")
		return false
	}
	return true
}
func traceFailure(c *gin.Context, err error) {
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		fail(c, 404, "NOT_FOUND", "Trace 不存在")
	case errors.Is(err, store.ErrTraceConflict):
		fail(c, 409, "TRACE_CONFLICT", "Trace 状态或事件冲突")
	case errors.Is(err, store.ErrTraceLimit):
		fail(c, 422, "TRACE_LIMIT_REACHED", "Trace 事件达到上限")
	default:
		fail(c, 503, "DEPENDENCY_UNAVAILABLE", "Trace 存储暂不可用")
	}
}

var traceKinds = map[string]bool{"user_message": true, "system_message": true, "model_start": true, "assistant_message": true, "tool_call": true, "tool_start": true, "tool_result": true, "tool_error": true, "tool_rejected": true, "model_error": true, "final_response": true}

func registerTraceRoutes(group *gin.RouterGroup, db *gorm.DB) {
	group.POST("/traces", func(c *gin.Context) {
		var input struct {
			traceIdentity
			ID        string `json:"trace_id"`
			RequestID string `json:"request_id"`
			Summary   string `json:"summary"`
			Model     string `json:"model"`
		}
		if !traceInput(c, &input) {
			return
		}
		if !input.valid() || !isUUID(input.ID) || !isUUID(input.RequestID) || len(input.Summary) > 500 || len(input.Model) > 100 {
			fail(c, 400, "INVALID_ARGUMENTS", "Trace 参数无效")
			return
		}
		now := time.Now().UTC()
		run := store.TraceRun{ID: input.ID, RequestID: input.RequestID, Role: input.Role, ActorID: input.ActorID, Summary: input.Summary, Model: input.Model, Status: "running", CreatedAt: now, UpdatedAt: now}
		if err := db.WithContext(c.Request.Context()).Create(&run).Error; err != nil {
			traceFailure(c, err)
			return
		}
		c.JSON(201, run)
	})
	group.POST("/traces/:id/events", func(c *gin.Context) {
		var input struct {
			traceIdentity
			EventID string          `json:"event_id"`
			Kind    string          `json:"kind"`
			Step    int             `json:"step"`
			CallID  string          `json:"call_id"`
			Payload json.RawMessage `json:"payload"`
		}
		if !traceInput(c, &input) {
			return
		}
		if !input.valid() || !isUUID(c.Param("id")) || !isUUID(input.EventID) || !traceKinds[input.Kind] || input.Step < 0 || input.Step > 100 || len(input.CallID) > 128 || len(input.Payload) > 16384 || !json.Valid(input.Payload) {
			fail(c, 400, "INVALID_ARGUMENTS", "Trace 事件无效")
			return
		}
		event := store.TraceEvent{EventID: input.EventID, Kind: input.Kind, Step: input.Step, CallID: input.CallID, Payload: input.Payload}
		if err := store.AppendTraceEvent(c.Request.Context(), db, input.Role, input.ActorID, c.Param("id"), &event); err != nil {
			traceFailure(c, err)
			return
		}
		c.JSON(201, event)
	})
	group.POST("/traces/:id/finish", func(c *gin.Context) {
		var input struct {
			traceIdentity
			Status     string `json:"status"`
			Incomplete bool   `json:"incomplete"`
			ErrorCode  string `json:"error_code"`
		}
		if !traceInput(c, &input) {
			return
		}
		if !input.valid() || !isUUID(c.Param("id")) || (input.Status != "succeeded" && input.Status != "failed" && input.Status != "cancelled") || !regexp.MustCompile(`^[A-Z0-9_]{0,80}$`).MatchString(input.ErrorCode) {
			fail(c, 400, "INVALID_ARGUMENTS", "Trace 终态无效")
			return
		}
		var run store.TraceRun
		query := traceQueryContext(c, db, input.traceIdentity)
		if err := query.First(&run, "id = ?", c.Param("id")).Error; err != nil {
			traceFailure(c, err)
			return
		}
		if run.Status != "running" {
			if run.Status != input.Status {
				traceFailure(c, store.ErrTraceConflict)
				return
			}
			c.JSON(200, run)
			return
		}
		now := time.Now().UTC()
		result := traceQueryContext(c, db, input.traceIdentity).Model(&store.TraceRun{}).Where("id = ? AND status = ?", run.ID, "running").Updates(map[string]any{"status": input.Status, "incomplete": input.Incomplete, "error_code": input.ErrorCode, "finished_at": now})
		if result.Error != nil {
			traceFailure(c, result.Error)
			return
		}
		if result.RowsAffected != 1 {
			traceFailure(c, store.ErrTraceConflict)
			return
		}
		if err := traceQueryContext(c, db, input.traceIdentity).First(&run, "id = ?", run.ID).Error; err != nil {
			traceFailure(c, err)
			return
		}
		c.JSON(200, run)
	})
	group.GET("/traces", func(c *gin.Context) {
		identity := traceIdentity{c.Query("role"), c.Query("actor_id")}
		offset, err := strconv.Atoi(c.DefaultQuery("offset", "0"))
		search := c.Query("search")
		if !identity.valid() || err != nil || offset < 0 || offset > 10000 || len(search) > 80 {
			fail(c, 400, "INVALID_ARGUMENTS", "Trace 查询无效")
			return
		}
		if err := store.InterruptStaleTraces(db, identity.Role, identity.ActorID); err != nil {
			traceFailure(c, err)
			return
		}
		query := traceQueryContext(c, db, identity).Model(&store.TraceRun{})
		if search != "" {
			query = query.Where("INSTR(request_id, ?) > 0 OR INSTR(id, ?) > 0", search, search)
		}
		var runs []store.TraceRun
		if err := query.Order("created_at DESC, id DESC").Offset(offset).Limit(31).Find(&runs).Error; err != nil {
			traceFailure(c, err)
			return
		}
		more := len(runs) > 30
		if more {
			runs = runs[:30]
		}
		c.JSON(200, gin.H{"traces": runs, "has_more": more})
	})
	group.GET("/traces/:id", func(c *gin.Context) {
		identity := traceIdentity{c.Query("role"), c.Query("actor_id")}
		after, err := strconv.Atoi(c.DefaultQuery("after_sequence", "0"))
		if !identity.valid() || !isUUID(c.Param("id")) || err != nil || after < 0 || after > 256 {
			fail(c, 400, "INVALID_ARGUMENTS", "Trace 查询无效")
			return
		}
		if err := store.InterruptStaleTraces(db, identity.Role, identity.ActorID); err != nil {
			traceFailure(c, err)
			return
		}
		var run store.TraceRun
		if err := traceQueryContext(c, db, identity).First(&run, "id = ?", c.Param("id")).Error; err != nil {
			traceFailure(c, err)
			return
		}
		var events []store.TraceEvent
		if err := db.WithContext(c.Request.Context()).Where("trace_id = ? AND sequence > ?", run.ID, after).Order("sequence").Limit(101).Find(&events).Error; err != nil {
			traceFailure(c, err)
			return
		}
		more := len(events) > 100
		if more {
			events = events[:100]
		}
		c.JSON(200, gin.H{"trace": run, "events": events, "has_more": more})
	})
}
func traceQueryContext(c *gin.Context, db *gorm.DB, identity traceIdentity) *gorm.DB {
	return store.TraceQuery(db.WithContext(c.Request.Context()), identity.Role, identity.ActorID)
}
