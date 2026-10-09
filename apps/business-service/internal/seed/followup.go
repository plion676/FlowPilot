package seed

import (
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"opspilot/business-service/internal/store"
	"time"
)

// AddFollowupDemo is additive: it never changes existing customer or ticket rows.
// Date-stamped codes allow a later day's demo without rewriting older records.
func AddFollowupDemo(db *gorm.DB, anchor time.Time) error {
	loc, _ := time.LoadLocation("Asia/Shanghai")
	day := anchor.In(loc).Format("2006-01-02")
	date, err := time.Parse("2006-01-02", day)
	if err != nil {
		return err
	}
	code := "C8" + date.Format("20060102")
	id := "followup-demo-" + date.Format("20060102")
	return db.Transaction(func(tx *gorm.DB) error {
		customer := store.Customer{ID: id, Code: code, Name: "模拟回访演示客户（" + day + "）", RenewalDate: date.AddDate(0, 0, 3), RiskLevel: "high", Status: "active"}
		if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&customer).Error; err != nil {
			return err
		}
		return tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&store.Ticket{ID: "followup-ticket-" + date.Format("20060102"), Code: "T8" + date.Format("20060102"), CustomerID: id, Title: "模拟回访演示：交付问题待核实", Status: "open", Severity: "high", OpenedAt: date}).Error
	})
}
