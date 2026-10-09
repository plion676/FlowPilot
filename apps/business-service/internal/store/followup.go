package store

import (
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"time"
)

type FollowupCandidate struct {
	Code        string `json:"customer_code"`
	RenewalDate string `json:"renewal_date"`
	RiskLevel   string `json:"risk_level"`
}
type FollowupDraftPayload struct {
	Candidates []FollowupCandidate `json:"candidates"`
}
type FollowupOperation struct {
	Operation   string `json:"operation"`
	TaskID      string `json:"task_id"`
	WindowStart string `json:"window_start,omitempty"`
	WindowEnd   string `json:"window_end,omitempty"`
}

func (r *TaskRepository) Propose(g TaskGrant, owner TaskIdentity, endpoint string, input FollowupOperation, request string) (map[string]any, error) {
	candidates := []FollowupCandidate{}
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		t, err := r.locked(tx, input.TaskID)
		if err != nil {
			return err
		}
		if err := r.checkGrant(tx, &t, g, owner, endpoint, "propose"); err != nil {
			return err
		}
		if input.Operation != "propose" || input.WindowStart != t.WindowStart || input.WindowEnd != t.WindowEnd {
			return TaskError("INVALID_ARGUMENTS")
		}
		var prior FollowupPlanDraft
		priorErr := tx.Where("task_id = ?", t.ID).First(&prior).Error
		if priorErr == nil {
			var payload FollowupDraftPayload
			if err := json.Unmarshal([]byte(prior.PayloadJSON), &payload); err != nil {
				return err
			}
			candidates = payload.Candidates
			return nil
		}
		if !errors.Is(priorErr, gorm.ErrRecordNotFound) {
			return priorErr
		}
		var customers []Customer
		if err := tx.Where("risk_level = ? AND status = ? AND renewal_date >= ? AND renewal_date <= ?", "high", "active", t.WindowStart, t.WindowEnd).Order("code").Limit(21).Find(&customers).Error; err != nil {
			return err
		}
		if len(customers) > 20 {
			return TaskError("CANDIDATE_LIMIT_REACHED")
		}
		for _, customer := range customers {
			candidates = append(candidates, FollowupCandidate{Code: customer.Code, RenewalDate: customer.RenewalDate.UTC().Format("2006-01-02"), RiskLevel: "high"})
		}
		if len(candidates) == 0 {
			t.ResultJSON = `{"proposal_empty":true}`
			if err := tx.Save(&t).Error; err != nil {
				return err
			}
			return r.audit(tx, &t, "no_candidates", request)
		}
		raw, _ := json.Marshal(FollowupDraftPayload{Candidates: candidates})
		if err := tx.Create(&FollowupPlanDraft{ID: uuid.NewString(), TaskID: t.ID, PayloadJSON: string(raw), Status: "draft", ExpiresAt: t.ExpiresAt}).Error; err != nil {
			return err
		}
		return r.audit(tx, &t, "draft_created", request)
	})
	return map[string]any{"task_id": input.TaskID, "operation": "propose", "status": "pending_approval", "candidates": candidates}, err
}

func (r *TaskRepository) Commit(g TaskGrant, owner TaskIdentity, endpoint, request string) (map[string]any, error) {
	count := 0
	err := r.DB.Transaction(func(tx *gorm.DB) error {
		t, err := r.locked(tx, g.TaskID)
		if err != nil {
			return err
		}
		if err := r.checkGrant(tx, &t, g, owner, endpoint, "commit"); err != nil {
			return err
		}
		var draft FollowupPlanDraft
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&draft, "task_id = ?", t.ID).Error; err != nil {
			return err
		}
		if draft.Status != "draft" || !r.Now().Before(draft.ExpiresAt) {
			return TaskError("TASK_EXPIRED")
		}
		var payload FollowupDraftPayload
		if err := json.Unmarshal([]byte(draft.PayloadJSON), &payload); err != nil {
			return err
		}
		if len(payload.Candidates) == 0 {
			return TaskError("INVALID_ARGUMENTS")
		}
		// Lock and recheck every approved customer before creating any official plan.
		customers := []Customer{}
		for _, candidate := range payload.Candidates {
			var customer Customer
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&customer, "code = ?", candidate.Code).Error; err != nil {
				return err
			}
			date := customer.RenewalDate.UTC().Format("2006-01-02")
			if customer.RiskLevel != "high" || customer.Status != "active" || date != candidate.RenewalDate || date < t.WindowStart || date > t.WindowEnd {
				return TaskError("CUSTOMER_CHANGED")
			}
			customers = append(customers, customer)
		}
		plans := []FollowupPlan{}
		ids := []string{}
		for _, customer := range customers {
			due, _ := time.Parse("2006-01-02", t.WindowStart)
			plan := FollowupPlan{ID: uuid.NewString(), TaskID: t.ID, CustomerID: customer.ID, Action: "核实工单影响与续费意向，记录客户反馈", DueDate: due, CreatedAt: r.Now().UTC()}
			plans = append(plans, plan)
			ids = append(ids, plan.ID)
		}
		if err := tx.Create(&plans).Error; err != nil {
			return err
		}
		draft.Status = "committed"
		if err := tx.Save(&draft).Error; err != nil {
			return err
		}
		raw, _ := json.Marshal(map[string]any{"plan_count": len(plans), "plan_ids": ids})
		t.ResultJSON = string(raw)
		count = len(plans)
		return r.move(tx, &t, "completed", "crm_committed", request)
	})
	return map[string]any{"task_id": g.TaskID, "operation": "commit", "status": "completed", "plan_count": count}, err
}
