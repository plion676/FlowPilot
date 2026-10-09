package store

import (
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"strings"
	"testing"
	"time"
)

func taskFixture(t *testing.T) (*TaskRepository, TaskIdentity, AgentTask) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := Migrate(db); err != nil {
		t.Fatal(err)
	}
	for _, name := range FollowupTools {
		if err := db.Where(AgentToolBinding{AgentID: "consultant", EndpointID: "business", ToolName: name}).FirstOrCreate(&AgentToolBinding{AgentID: "consultant", EndpointID: "business", ToolName: name}).Error; err != nil {
			t.Fatal(err)
		}
	}
	now := time.Date(2026, 9, 30, 0, 0, 0, 0, time.UTC)
	repo := NewTaskRepository(db)
	repo.Now = func() time.Time { return now }
	owner := TaskIdentity{Role: "consultant", ActorID: "demo-consultant"}
	var profile AgentProfile
	if err := db.First(&profile, "id = ?", owner.Role).Error; err != nil {
		t.Fatal(err)
	}
	task, err := repo.Create(owner, uuid.NewString(), "business", profile.Version, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	return repo, owner, task
}

func TestTaskPersistenceIdentityAndIdempotency(t *testing.T) {
	r, owner, task := taskFixture(t)
	again, err := r.Create(owner, task.RequestKey, "business", task.BindingVersion, uuid.NewString())
	if err != nil || again.ID != task.ID {
		t.Fatal("create idempotency", err)
	}
	replacement := NewTaskRepository(r.DB)
	replacement.Now = r.Now
	if _, err := replacement.Get(task.ID, owner); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Get(task.ID, TaskIdentity{Role: "consultant", ActorID: "another"}); err != TaskError("NOT_FOUND") {
		t.Fatal("cross user", err)
	}
	if task.WindowStart != "2026-09-30" || task.WindowEnd != "2026-10-07" {
		t.Fatal(task)
	}
}
func TestTaskDecisionStateAndCancellation(t *testing.T) {
	r, owner, task := taskFixture(t)
	if _, err := r.Decide(task.ID, owner, uuid.NewString(), "approve", task.Version, uuid.NewString()); err != TaskError("TASK_CONFLICT") {
		t.Fatal(err)
	}
	claimed, g, err := r.Claim(uuid.NewString())
	if err != nil || claimed.Status != "gathering" {
		t.Fatal(err)
	}
	key := uuid.NewString()
	cancelled, err := r.Decide(task.ID, owner, key, "cancel", claimed.Version, uuid.NewString())
	if err != nil || cancelled.Status != "cancelled" {
		t.Fatal(err)
	}
	again, err := r.Decide(task.ID, owner, key, "cancel", claimed.Version, uuid.NewString())
	if err != nil || again.Version != cancelled.Version {
		t.Fatal(err)
	}
	if err := r.Heartbeat(*g); err != TaskError("LEASE_LOST") {
		t.Fatal(err)
	}
	if _, err := r.Decide(task.ID, owner, uuid.NewString(), "approve", cancelled.Version, uuid.NewString()); err != TaskError("TASK_CONFLICT") {
		t.Fatal(err)
	}
}
func TestLeaseRecoveryExpiryAndAudit(t *testing.T) {
	r, owner, task := taskFixture(t)
	first, g, err := r.Claim(uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	if next, _, err := r.Claim(uuid.NewString()); err != nil || next != nil {
		t.Fatal("live lease reclaimed", err)
	}
	now := r.Now().Add(3 * time.Minute)
	r.Now = func() time.Time { return now }
	second, newGrant, err := r.Claim(uuid.NewString())
	if err != nil || second.Version <= first.Version || newGrant.LeaseToken == g.LeaseToken {
		t.Fatal(err)
	}
	if err := r.Heartbeat(*g); err != TaskError("LEASE_LOST") {
		t.Fatal(err)
	}
	var events []TaskAudit
	r.DB.Find(&events)
	for _, event := range events {
		if strings.Contains(event.Event, g.LeaseToken) {
			t.Fatal("secret leaked")
		}
	}
	now = now.Add(25 * time.Hour)
	expired, err := r.Get(task.ID, owner)
	if err != nil || expired.Status != "expired" {
		t.Fatal(err)
	}
	if _, err := r.Decide(task.ID, owner, uuid.NewString(), "approve", expired.Version, uuid.NewString()); err != TaskError("TASK_EXPIRED") {
		t.Fatal(err)
	}
}

func TestLeaseRecoveryHasBoundedAttempts(t *testing.T) {
	r, owner, task := taskFixture(t)
	for range 3 {
		claimed, _, err := r.Claim(uuid.NewString())
		if err != nil || claimed == nil {
			t.Fatal("recovery claim failed", err)
		}
		now := r.Now().Add(3 * time.Minute)
		r.Now = func() time.Time { return now }
	}
	claimed, _, err := r.Claim(uuid.NewString())
	if err != nil || claimed != nil {
		t.Fatal("retry limit ignored", err)
	}
	failed, err := r.Get(task.ID, owner)
	if err != nil || failed.Status != "failed" || failed.ErrorCode != "TASK_RETRY_EXHAUSTED" {
		t.Fatal("retry exhaustion not persisted")
	}
}

func TestPendingApprovalRejectsStaleBinding(t *testing.T) {
	r, owner, task, _ := preparedFollowup(t)
	if err := r.DB.Model(&AgentProfile{}).Where("id = ?", owner.Role).Update("version", task.BindingVersion+1).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := r.Decide(task.ID, owner, uuid.NewString(), "approve", task.Version, uuid.NewString()); err != TaskError("BINDING_CHANGED") {
		t.Fatal("stale binding approval accepted", err)
	}
	var count int64
	r.DB.Model(&TaskDecision{}).Where("task_id = ? AND decision = ?", task.ID, "approve").Count(&count)
	if count != 0 {
		t.Fatal("invalid approval persisted")
	}
}
