package store

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// Observations are never a source of execution authority.
type TraceRun struct {
	ID           string     `gorm:"primaryKey;size:36" json:"trace_id"`
	RequestID    string     `gorm:"size:36;index" json:"request_id"`
	Role         string     `gorm:"size:32;index" json:"role"`
	ActorID      string     `gorm:"size:80;index" json:"-"`
	Summary      string     `gorm:"size:500" json:"summary"`
	Model        string     `gorm:"size:100" json:"model"`
	Status       string     `gorm:"size:20" json:"status"`
	Incomplete   bool       `json:"incomplete"`
	ErrorCode    string     `gorm:"size:80" json:"error_code"`
	LastSequence int        `json:"last_sequence"`
	CreatedAt    time.Time  `json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
	FinishedAt   *time.Time `json:"finished_at"`
}

type TraceEvent struct {
	TraceID   string          `gorm:"primaryKey;size:36" json:"-"`
	Sequence  int             `gorm:"primaryKey" json:"sequence"`
	EventID   string          `gorm:"uniqueIndex;size:36" json:"event_id"`
	Kind      string          `gorm:"size:32" json:"kind"`
	Step      int             `json:"step"`
	CallID    string          `gorm:"size:128" json:"call_id"`
	Payload   json.RawMessage `gorm:"type:json" json:"payload"`
	CreatedAt time.Time       `json:"created_at"`
}

var ErrTraceConflict = errors.New("trace conflict")
var ErrTraceLimit = errors.New("trace event limit")

func TraceQuery(db *gorm.DB, role, actor string) *gorm.DB {
	return db.Where("role = ? AND actor_id = ?", role, actor)
}

func AppendTraceEvent(ctx context.Context, db *gorm.DB, role, actor, id string, event *TraceEvent) error {
	return db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var run TraceRun
		if err := TraceQuery(tx, role, actor).Clauses(clause.Locking{Strength: "UPDATE"}).First(&run, "id = ?", id).Error; err != nil {
			return err
		}
		var existing TraceEvent
		if err := tx.First(&existing, "event_id = ?", event.EventID).Error; err == nil {
			var a, b any
			json.Unmarshal(existing.Payload, &a)
			json.Unmarshal(event.Payload, &b)
			x, _ := json.Marshal(a)
			y, _ := json.Marshal(b)
			if existing.TraceID != id || existing.Kind != event.Kind || existing.Step != event.Step || existing.CallID != event.CallID || string(x) != string(y) {
				return ErrTraceConflict
			}
			*event = existing
			return nil
		} else if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if run.Status != "running" {
			return ErrTraceConflict
		}
		if run.LastSequence >= 256 {
			return ErrTraceLimit
		}
		event.TraceID, event.Sequence = id, run.LastSequence+1
		event.CreatedAt = time.Now().UTC()
		if err := tx.Create(event).Error; err != nil {
			return err
		}
		return tx.Model(&run).Updates(map[string]any{"last_sequence": event.Sequence, "updated_at": time.Now().UTC()}).Error
	})
}

func InterruptStaleTraces(db *gorm.DB, role, actor string) error {
	now := time.Now().UTC()
	return TraceQuery(db.Model(&TraceRun{}), role, actor).Where("status = ? AND updated_at < ?", "running", now.Add(-15*time.Minute)).Updates(map[string]any{"status": "interrupted", "incomplete": true, "error_code": "TRACE_INTERRUPTED", "finished_at": now}).Error
}
