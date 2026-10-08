package seed

import (
	"fmt"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"opspilot/business-service/internal/store"
)

const Version = "demo-v1"

func Seed(db *gorm.DB, anchor time.Time) error {
	anchor = time.Date(anchor.UTC().Year(), anchor.UTC().Month(), anchor.UTC().Day(), 0, 0, 0, 0, time.UTC)
	return db.Transaction(func(tx *gorm.DB) error {
		customers := []store.Customer{
			{ID: "10000000-0000-4000-8000-000000000001", Code: "C1001", Name: "模拟客户甲", RenewalDate: anchor.AddDate(0, 0, 5), RiskLevel: "high", Status: "active"},
			{ID: "10000000-0000-4000-8000-000000000002", Code: "C1002", Name: "模拟客户乙", RenewalDate: anchor.AddDate(0, 0, 20), RiskLevel: "low", Status: "active"},
			{ID: "10000000-0000-4000-8000-000000000003", Code: "C1003", Name: "模拟客户丙", RenewalDate: anchor.AddDate(0, 0, 3), RiskLevel: "high", Status: "active"},
		}
		for _, customer := range customers {
			if err := tx.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "code"}}, DoUpdates: clause.AssignmentColumns([]string{"name", "renewal_date", "risk_level", "status"})}).Create(&customer).Error; err != nil {
				return fmt.Errorf("upsert customer %s: %w", customer.Code, err)
			}
		}
		tickets := []store.Ticket{
			{ID: "20000000-0000-4000-8000-000000000001", Code: "T1001", CustomerID: customers[0].ID, Title: "模拟交付问题待处理", Status: "open", Severity: "high", OpenedAt: anchor.AddDate(0, 0, -2)},
			{ID: "20000000-0000-4000-8000-000000000002", Code: "T1002", CustomerID: customers[0].ID, Title: "模拟咨询已处理", Status: "closed", Severity: "low", OpenedAt: anchor.AddDate(0, 0, -8)},
			{ID: "20000000-0000-4000-8000-000000000003", Code: "T1003", CustomerID: customers[2].ID, Title: "模拟配置问题待处理", Status: "open", Severity: "medium", OpenedAt: anchor.AddDate(0, 0, -1)},
		}
		for _, ticket := range tickets {
			if err := tx.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "code"}}, DoUpdates: clause.AssignmentColumns([]string{"title", "status", "severity", "opened_at"})}).Create(&ticket).Error; err != nil {
				return fmt.Errorf("upsert ticket %s: %w", ticket.Code, err)
			}
		}
		workOrders := []store.WorkOrder{
			{ID: "30000000-0000-4000-8000-000000000001", Code: "WO-1001", CustomerID: customers[0].ID, Status: "in_progress", PlannedDeliveryDate: anchor.AddDate(0, 0, 6), UpdatedAt: anchor},
			{ID: "30000000-0000-4000-8000-000000000002", Code: "WO-1002", CustomerID: customers[1].ID, Status: "completed", PlannedDeliveryDate: anchor.AddDate(0, 0, -1), UpdatedAt: anchor},
		}
		for _, order := range workOrders {
			if err := tx.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "code"}}, DoUpdates: clause.AssignmentColumns([]string{"status", "planned_delivery_date", "updated_at"})}).Create(&order).Error; err != nil {
				return fmt.Errorf("upsert work order %s: %w", order.Code, err)
			}
		}
		return nil
	})
}
