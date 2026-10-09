package store

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const FollowupSkill = "crm.followup_workflow"

var FollowupTools = []string{"crm.get_customer_overview", "crm.list_open_tickets", "knowledge.search_sop", "workflow.create_followup_plan"}
var terminalTasks = map[string]bool{"completed": true, "completed_empty": true, "failed": true, "rejected": true, "cancelled": true, "expired": true}

type TaskError string

func (e TaskError) Error() string { return string(e) }

type AgentTask struct {
	ID               string    `gorm:"type:varchar(36);primaryKey" json:"task_id"`
	Role             string    `gorm:"type:varchar(80);index" json:"role"`
	ActorID          string    `gorm:"type:varchar(80);uniqueIndex:idx_task_request" json:"-"`
	RequestKey       string    `gorm:"type:varchar(36);uniqueIndex:idx_task_request" json:"-"`
	EndpointID       string    `gorm:"type:varchar(32)" json:"endpoint_id"`
	BindingVersion   uint64    `json:"-"`
	EndpointRevision uint64    `json:"-"`
	Status           string    `gorm:"type:varchar(24);index" json:"status"`
	Version          uint64    `json:"version"`
	WindowStart      string    `gorm:"type:varchar(10)" json:"window_start"`
	WindowEnd        string    `gorm:"type:varchar(10)" json:"window_end"`
	ProposalJSON     string    `gorm:"type:json" json:"-"`
	ResultJSON       string    `gorm:"type:json" json:"-"`
	ErrorCode        string    `gorm:"type:varchar(80)" json:"error_code"`
	LeaseHash        string    `gorm:"type:varchar(64)" json:"-"`
	LeaseUntil       time.Time `json:"-"`
	Attempts         int       `json:"-"`
	ExpiresAt        time.Time `json:"expires_at"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type TaskDecision struct {
	ID              string `gorm:"type:varchar(36);primaryKey"`
	TaskID          string `gorm:"type:varchar(36);uniqueIndex:idx_task_decision"`
	Key             string `gorm:"type:varchar(36);uniqueIndex:idx_task_decision"`
	ActorID         string `gorm:"type:varchar(80)"`
	Decision        string `gorm:"type:varchar(16)"`
	ExpectedVersion uint64
	CreatedAt       time.Time
}

type TaskAudit struct {
	ID        string    `gorm:"type:varchar(36);primaryKey" json:"id"`
	TaskID    string    `gorm:"type:varchar(36);index" json:"task_id"`
	RequestID string    `gorm:"type:varchar(36)" json:"request_id"`
	ActorID   string    `gorm:"type:varchar(80)" json:"actor_id"`
	Event     string    `gorm:"type:varchar(40)" json:"event"`
	Version   uint64    `json:"version"`
	CreatedAt time.Time `json:"created_at"`
}

type TaskIdentity struct {
	Role    string `json:"role"`
	ActorID string `json:"actor_id"`
}
type TaskGrant struct {
	TaskID     string `json:"task_id"`
	Version    uint64 `json:"version"`
	LeaseToken string `json:"lease_token"`
}
type TaskRepository struct {
	DB  *gorm.DB
	Now func() time.Time
}

func NewTaskRepository(db *gorm.DB) *TaskRepository { return &TaskRepository{DB: db, Now: time.Now} }

func (r *TaskRepository) audit(tx *gorm.DB, t *AgentTask, event, request string) error {
	return tx.Create(&TaskAudit{ID: uuid.NewString(), TaskID: t.ID, RequestID: request, ActorID: t.ActorID, Event: event, Version: t.Version, CreatedAt: r.Now().UTC()}).Error
}
func (r *TaskRepository) move(tx *gorm.DB, t *AgentTask, state, event, request string) error {
	t.Status = state
	t.Version++
	t.UpdatedAt = r.Now().UTC()
	if terminalTasks[state] || state == "pending_approval" {
		t.LeaseHash = ""
	}
	if state == "pending_approval" {
		t.Attempts = 0
	}
	if err := tx.Save(t).Error; err != nil {
		return err
	}
	return r.audit(tx, t, event, request)
}
func (r *TaskRepository) authorized(tx *gorm.DB, t *AgentTask) error {
	if t.Role != "consultant" {
		return TaskError("FORBIDDEN_SKILL")
	}
	var p AgentProfile
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&p, "id = ?", t.Role).Error; err != nil {
		return err
	}
	var ep MCPEndpoint
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&ep, "id = ?", t.EndpointID).Error; err != nil {
		return err
	}
	if !ep.Enabled || ep.RoleID != t.Role || ep.ExecutionRevision != t.EndpointRevision || p.Version != t.BindingVersion {
		return TaskError("BINDING_CHANGED")
	}
	var count int64
	if err := tx.Model(&AgentToolBinding{}).Where("agent_id = ? AND endpoint_id = ? AND tool_name IN ?", t.Role, t.EndpointID, FollowupTools).Count(&count).Error; err != nil {
		return err
	}
	if count != 4 {
		return TaskError("FORBIDDEN_TOOL")
	}
	return nil
}
func owned(t *AgentTask, id TaskIdentity) error {
	if t.Role != id.Role || t.ActorID != id.ActorID {
		return TaskError("NOT_FOUND")
	}
	return nil
}
func (r *TaskRepository) locked(tx *gorm.DB, id string) (AgentTask, error) {
	var t AgentTask
	err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&t, "id = ?", id).Error
	return t, err
}

func (r *TaskRepository) Create(id TaskIdentity, key, endpoint string, version uint64, request string) (AgentTask, error) {
	var task AgentTask
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		// Serialize per role before the idempotency lookup, including concurrent creates.
		var p AgentProfile
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&p, "id = ?", id.Role).Error; err != nil {
			return err
		}
		err := tx.Where("actor_id = ? AND request_key = ?", id.ActorID, key).First(&task).Error
		if err == nil {
			if task.Role != id.Role || task.EndpointID != endpoint {
				return TaskError("TASK_CONFLICT")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var ep MCPEndpoint
		if err := tx.First(&ep, "id = ?", endpoint).Error; err != nil {
			return err
		}
		loc, err := time.LoadLocation("Asia/Shanghai")
		if err != nil {
			return err
		}
		now := r.Now()
		today := now.In(loc)
		task = AgentTask{ID: uuid.NewString(), Role: id.Role, ActorID: id.ActorID, RequestKey: key, EndpointID: endpoint, BindingVersion: version, EndpointRevision: ep.ExecutionRevision, Status: "created", Version: 1, WindowStart: today.Format("2006-01-02"), WindowEnd: today.AddDate(0, 0, 7).Format("2006-01-02"), ProposalJSON: "{}", ResultJSON: "{}", LeaseUntil: now.UTC(), ExpiresAt: now.UTC().Add(24 * time.Hour), CreatedAt: now.UTC(), UpdatedAt: now.UTC()}
		if err := r.authorized(tx, &task); err != nil {
			return err
		}
		var count int64
		if err := tx.Model(&AgentTask{}).Where("actor_id = ? AND status NOT IN ?", id.ActorID, []string{"completed", "completed_empty", "failed", "rejected", "cancelled", "expired"}).Count(&count).Error; err != nil {
			return err
		}
		if count >= 20 {
			return TaskError("TASK_LIMIT_REACHED")
		}
		if err := tx.Create(&task).Error; err != nil {
			return err
		}
		return r.audit(tx, &task, "created", request)
	})
	return task, err
}

func (r *TaskRepository) Get(id string, owner TaskIdentity) (AgentTask, error) {
	var task AgentTask
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		var err error
		task, err = r.locked(tx, id)
		if err != nil {
			return err
		}
		if err := owned(&task, owner); err != nil {
			return err
		}
		if !terminalTasks[task.Status] && !r.Now().Before(task.ExpiresAt) {
			return r.move(tx, &task, "expired", "expired", uuid.NewString())
		}
		return nil
	})
	return task, err
}
func (r *TaskRepository) List(owner TaskIdentity) ([]AgentTask, error) {
	tasks := []AgentTask{}
	if err := r.DB.Where("actor_id = ? AND role = ?", owner.ActorID, owner.Role).Order("created_at DESC, id DESC").Limit(50).Find(&tasks).Error; err != nil {
		return nil, err
	}
	for i := range tasks {
		t, err := r.Get(tasks[i].ID, owner)
		if err != nil {
			return nil, err
		}
		tasks[i] = t
	}
	return tasks, nil
}
func (r *TaskRepository) Decide(id string, owner TaskIdentity, key, decision string, version uint64, request string) (AgentTask, error) {
	if _, err := r.Get(id, owner); err != nil {
		return AgentTask{}, err
	}
	var task AgentTask
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		var err error
		task, err = r.locked(tx, id)
		if err != nil {
			return err
		}
		if err := owned(&task, owner); err != nil {
			return err
		}
		var prior TaskDecision
		err = tx.Where("task_id = ? AND `key` = ?", id, key).First(&prior).Error
		if err == nil {
			if prior.Decision != decision || prior.ActorID != owner.ActorID || prior.ExpectedVersion != version {
				return TaskError("TASK_CONFLICT")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if task.Status == "expired" || !r.Now().Before(task.ExpiresAt) {
			return TaskError("TASK_EXPIRED")
		}
		if task.Version != version || terminalTasks[task.Status] {
			return TaskError("TASK_CONFLICT")
		}
		next := ""
		switch decision {
		case "approve":
			if task.Status == "pending_approval" {
				if err := r.authorized(tx, &task); err != nil {
					return err
				}
				next = "approved"
			}
		case "reject":
			if task.Status == "pending_approval" {
				next = "rejected"
			}
		case "cancel":
			if task.Status == "created" || task.Status == "gathering" || task.Status == "pending_approval" || task.Status == "approved" {
				next = "cancelled"
			}
		}
		if next == "" {
			return TaskError("TASK_CONFLICT")
		}
		if err := tx.Create(&TaskDecision{ID: uuid.NewString(), TaskID: id, Key: key, ActorID: owner.ActorID, Decision: decision, ExpectedVersion: version, CreatedAt: r.Now().UTC()}).Error; err != nil {
			return err
		}
		return r.move(tx, &task, next, decision, request)
	})
	return task, err
}

func hashToken(token string) string {
	h := sha256.Sum256([]byte(token))
	return hex.EncodeToString(h[:])
}
func (r *TaskRepository) lease(t *AgentTask, g TaskGrant) error {
	if t.ID != g.TaskID || t.Version != g.Version || len(g.LeaseToken) != 64 || t.LeaseHash == "" || subtle.ConstantTimeCompare([]byte(t.LeaseHash), []byte(hashToken(g.LeaseToken))) != 1 || !r.Now().Before(t.LeaseUntil) {
		return TaskError("LEASE_LOST")
	}
	if !r.Now().Before(t.ExpiresAt) {
		return TaskError("TASK_EXPIRED")
	}
	return nil
}

func (r *TaskRepository) Claim(request string) (*AgentTask, *TaskGrant, error) {
	var task AgentTask
	var grant TaskGrant
	found := false
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		var tasks []AgentTask
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("status IN ?", []string{"created", "gathering", "approved", "committing"}).Order("created_at, id").Limit(50).Find(&tasks).Error; err != nil {
			return err
		}
		for _, t := range tasks {
			if !r.Now().Before(t.ExpiresAt) {
				if err := r.move(tx, &t, "expired", "expired", request); err != nil {
					return err
				}
				continue
			}
			if (t.Status == "gathering" || t.Status == "committing") && r.Now().Before(t.LeaseUntil) {
				continue
			}
			if t.Attempts >= 3 {
				t.ErrorCode = "TASK_RETRY_EXHAUSTED"
				if err := r.move(tx, &t, "failed", "retry_exhausted", request); err != nil {
					return err
				}
				continue
			}
			buf := make([]byte, 32)
			if _, err := rand.Read(buf); err != nil {
				return err
			}
			token := hex.EncodeToString(buf)
			t.LeaseHash = hashToken(token)
			t.LeaseUntil = r.Now().UTC().Add(2 * time.Minute)
			t.Attempts++
			next := "gathering"
			if t.Status == "approved" || t.Status == "committing" {
				next = "committing"
			}
			if err := r.move(tx, &t, next, "claimed_"+next, request); err != nil {
				return err
			}
			task = t
			grant = TaskGrant{TaskID: t.ID, Version: t.Version, LeaseToken: token}
			found = true
			return nil
		}
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	if !found {
		return nil, nil, nil
	}
	return &task, &grant, nil
}
func (r *TaskRepository) Heartbeat(g TaskGrant) error {
	return r.DB.Transaction(func(tx *gorm.DB) error {
		t, err := r.locked(tx, g.TaskID)
		if err != nil {
			return err
		}
		if err := r.lease(&t, g); err != nil {
			return err
		}
		t.LeaseUntil = r.Now().UTC().Add(2 * time.Minute)
		return tx.Save(&t).Error
	})
}
func (r *TaskRepository) Finish(g TaskGrant, proposal json.RawMessage, errorCode, request string) error {
	return r.DB.Transaction(func(tx *gorm.DB) error {
		t, err := r.locked(tx, g.TaskID)
		if err != nil {
			return err
		}
		if err := r.lease(&t, g); err != nil {
			return err
		}
		if t.Status != "gathering" && t.Status != "committing" {
			return TaskError("TASK_CONFLICT")
		}
		if errorCode != "" {
			t.ErrorCode = errorCode
			return r.move(tx, &t, "failed", "failed", request)
		}
		if t.Status != "gathering" || len(proposal) > 100000 || !json.Valid(proposal) {
			return TaskError("INVALID_ARGUMENTS")
		}
		if err := r.authorized(tx, &t); err != nil {
			return err
		}
		var draft FollowupPlanDraft
		draftErr := tx.First(&draft, "task_id = ?", t.ID).Error
		var emptyResult struct {
			Empty bool `json:"proposal_empty"`
		}
		_ = json.Unmarshal([]byte(t.ResultJSON), &emptyResult)
		if errors.Is(draftErr, gorm.ErrRecordNotFound) && emptyResult.Empty {
			t.ProposalJSON = string(proposal)
			t.ResultJSON = `{"plan_count":0}`
			return r.move(tx, &t, "completed_empty", "completed_empty", request)
		}
		if draftErr != nil {
			return draftErr
		}
		var payload struct {
			Candidates []json.RawMessage `json:"candidates"`
		}
		if err := json.Unmarshal([]byte(draft.PayloadJSON), &payload); err != nil {
			return err
		}
		t.ProposalJSON = string(proposal)
		status := "pending_approval"
		if len(payload.Candidates) == 0 {
			status = "completed_empty"
		}
		return r.move(tx, &t, status, status, request)
	})
}
func (r *TaskRepository) Authorize(g TaskGrant, owner TaskIdentity, endpoint, operation string) error {
	return r.DB.Transaction(func(tx *gorm.DB) error {
		t, err := r.locked(tx, g.TaskID)
		if err != nil {
			return err
		}
		return r.checkGrant(tx, &t, g, owner, endpoint, operation)
	})
}
func (r *TaskRepository) checkGrant(tx *gorm.DB, t *AgentTask, g TaskGrant, owner TaskIdentity, endpoint, operation string) error {
	if err := owned(t, owner); err != nil {
		return err
	}
	if err := r.lease(t, g); err != nil {
		return err
	}
	if t.EndpointID != endpoint {
		return TaskError("FORBIDDEN_TOOL")
	}
	if operation == "propose" && t.Status != "gathering" {
		return TaskError("APPROVAL_REQUIRED")
	}
	if operation == "commit" {
		if t.Status != "committing" {
			return TaskError("APPROVAL_REQUIRED")
		}
		var count int64
		if err := tx.Model(&TaskDecision{}).Where("task_id = ? AND decision = ?", t.ID, "approve").Count(&count).Error; err != nil {
			return err
		}
		if count != 1 {
			return TaskError("APPROVAL_REQUIRED")
		}
	}
	if operation != "propose" && operation != "commit" {
		return TaskError("INVALID_ARGUMENTS")
	}
	return r.authorized(tx, t)
}
