package store

import (
	"encoding/json"
	"github.com/google/uuid"
	"testing"
	"time"
)

func preparedFollowup(t *testing.T) (*TaskRepository, TaskIdentity, AgentTask, TaskGrant) {
	t.Helper()
	r, owner, task := taskFixture(t)
	for i, code := range []string{"C9001", "C9002"} {
		if err := r.DB.Create(&Customer{ID: uuid.NewString(), Code: code, Name: "模拟回访客户", RenewalDate: r.Now().AddDate(0, 0, i+2), RiskLevel: "high", Status: "active"}).Error; err != nil {
			t.Fatal(err)
		}
	}
	_, grant, err := r.Claim(uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	result, err := r.Propose(*grant, owner, "business", FollowupOperation{Operation: "propose", TaskID: task.ID, WindowStart: task.WindowStart, WindowEnd: task.WindowEnd}, uuid.NewString())
	if err != nil || len(result["candidates"].([]FollowupCandidate)) != 2 {
		t.Fatal(result, err)
	}
	var count int64
	r.DB.Model(&FollowupPlan{}).Count(&count)
	if count != 0 {
		t.Fatal("unapproved plans")
	}
	if _, err := r.Commit(*grant, owner, "business", uuid.NewString()); err != TaskError("APPROVAL_REQUIRED") {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"analysis": "模拟建议", "citations": []any{}})
	if err := r.Finish(*grant, raw, "", uuid.NewString()); err != nil {
		t.Fatal(err)
	}
	task, err = r.Get(task.ID, owner)
	if err != nil {
		t.Fatal(err)
	}
	return r, owner, task, *grant
}
func TestFollowupApprovalCommitAtomicAndIdempotent(t *testing.T) {
	r, owner, task, _ := preparedFollowup(t)
	key := uuid.NewString()
	approved, err := r.Decide(task.ID, owner, key, "approve", task.Version, uuid.NewString())
	if err != nil || approved.Status != "approved" {
		t.Fatal(err)
	}
	_, g, err := r.Claim(uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	forged := *g
	forged.LeaseToken = "bad"
	if err := r.Authorize(forged, owner, "business", "commit"); err != TaskError("LEASE_LOST") {
		t.Fatal(err)
	}
	result, err := r.Commit(*g, owner, "business", uuid.NewString())
	if err != nil || result["plan_count"] != 2 {
		t.Fatal(result, err)
	}
	again, err := r.Decide(task.ID, owner, key, "approve", task.Version, uuid.NewString())
	if err != nil || again.Status != "completed" {
		t.Fatal(err)
	}
	if _, err := r.Commit(*g, owner, "business", uuid.NewString()); err != TaskError("LEASE_LOST") {
		t.Fatal(err)
	}
	var count int64
	r.DB.Model(&FollowupPlan{}).Count(&count)
	if count != 2 {
		t.Fatal(count)
	}
}
func TestFollowupRejectCancelAndChangedCustomerNeverWrite(t *testing.T) {
	for _, decision := range []string{"reject", "cancel", "changed", "revoked", "expired"} {
		t.Run(decision, func(t *testing.T) {
			r, owner, task, _ := preparedFollowup(t)
			if decision == "reject" || decision == "cancel" {
				if _, err := r.Decide(task.ID, owner, uuid.NewString(), decision, task.Version, uuid.NewString()); err != nil {
					t.Fatal(err)
				}
			} else {
				if _, err := r.Decide(task.ID, owner, uuid.NewString(), "approve", task.Version, uuid.NewString()); err != nil {
					t.Fatal(err)
				}
				_, g, err := r.Claim(uuid.NewString())
				if err != nil {
					t.Fatal(err)
				}
				expected := TaskError("CUSTOMER_CHANGED")
				if decision == "changed" {
					r.DB.Model(&Customer{}).Where("code = ?", "C9002").Update("risk_level", "low")
				}
				if decision == "revoked" {
					r.DB.Model(&AgentProfile{}).Where("id = ?", "consultant").Update("version", task.BindingVersion+1)
					expected = "BINDING_CHANGED"
				}
				if decision == "expired" {
					now := r.Now().Add(25 * time.Hour)
					r.Now = func() time.Time { return now }
					expected = "LEASE_LOST"
				}
				if _, err := r.Commit(*g, owner, "business", uuid.NewString()); err != expected {
					t.Fatal(err)
				}
			}
			var count int64
			r.DB.Model(&FollowupPlan{}).Count(&count)
			if count != 0 {
				t.Fatal("partial or unapproved write", count)
			}
		})
	}
}
func TestEmptyFollowupNoDraftOrPlan(t *testing.T) {
	r, owner, task := taskFixture(t)
	_, g, _ := r.Claim(uuid.NewString())
	_, err := r.Propose(*g, owner, "business", FollowupOperation{Operation: "propose", TaskID: task.ID, WindowStart: task.WindowStart, WindowEnd: task.WindowEnd}, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	// MySQL normalizes JSON whitespace; empty-result detection must decode JSON.
	if err := r.DB.Model(&AgentTask{}).Where("id = ?", task.ID).Update("result_json", `{"proposal_empty": true}`).Error; err != nil {
		t.Fatal(err)
	}
	if err := r.Finish(*g, json.RawMessage(`{"candidates":[]}`), "", uuid.NewString()); err != nil {
		t.Fatal(err)
	}
	empty, _ := r.Get(task.ID, owner)
	if empty.Status != "completed_empty" {
		t.Fatal(empty.Status)
	}
	var count int64
	r.DB.Model(&FollowupPlanDraft{}).Count(&count)
	if count != 0 {
		t.Fatal(count)
	}
}
