package store

import (
	"fmt"
	"os"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func OpenMySQLFromEnv() (*gorm.DB, error) {
	dsn := os.Getenv("BUSINESS_DATABASE_DSN")
	if dsn == "" {
		return nil, fmt.Errorf("BUSINESS_DATABASE_DSN 未配置")
	}
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{})
	if err != nil {
		return nil, fmt.Errorf("无法连接模拟业务数据库")
	}
	return db, nil
}

func Migrate(db *gorm.DB) error {
	if err := db.AutoMigrate(&TraceRun{}, &TraceEvent{}); err != nil {
		return err
	}
	if err := db.AutoMigrate(&AgentTask{}, &TaskDecision{}, &TaskAudit{}, &Customer{}, &Ticket{}, &WorkOrder{}, &FollowupPlanDraft{}, &FollowupPlan{}, &AgentProfile{}, &AgentBindingAudit{}, &MCPEndpoint{}, &AgentEndpointBinding{}, &AgentToolBinding{}, &MCPEndpointCheck{}, &MCPEndpointAudit{}, &ConfigMigration{}); err != nil {
		return err
	}
	// Startup must never restore a tool that an administrator removed.
	if err := db.Clauses(clause.OnConflict{DoNothing: true}).Create(&AgentProfile{
		ID: "consultant", Name: "运营顾问", Version: 1,
		BoundToolsJSON: `["crm.get_customer_overview","crm.list_open_tickets"]`,
	}).Error; err != nil {
		return err
	}
	if err := MigrateEndpointBindings(db); err != nil {
		return err
	}
	return MigrateRoleEndpoints(db)
}
