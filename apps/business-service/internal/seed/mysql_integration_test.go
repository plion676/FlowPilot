package seed

import (
	"os"
	"testing"
	"time"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

func TestSeedOnMySQL(t *testing.T) {
	dsn := os.Getenv("OPSPILOT_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("设置 OPSPILOT_TEST_MYSQL_DSN 后执行真实 MySQL 集成测试")
	}
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatal("无法连接测试 MySQL")
	}
	if err := store.Migrate(db); err != nil {
		t.Fatal(err)
	}
	anchor := time.Date(2026, 9, 30, 0, 0, 0, 0, time.UTC)
	for range 2 {
		if err := Seed(db, anchor); err != nil {
			t.Fatal(err)
		}
	}
	var customerCount, ticketCount, orderCount int64
	if err := db.Model(&store.Customer{}).Where("code IN ?", []string{"C1001", "C1002", "C1003"}).Count(&customerCount).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&store.Ticket{}).Where("code IN ?", []string{"T1001", "T1002", "T1003"}).Count(&ticketCount).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&store.WorkOrder{}).Where("code IN ?", []string{"WO-1001", "WO-1002"}).Count(&orderCount).Error; err != nil {
		t.Fatal(err)
	}
	if customerCount != 3 || ticketCount != 3 || orderCount != 2 {
		t.Fatalf("unexpected seed counts: customers=%d tickets=%d work_orders=%d", customerCount, ticketCount, orderCount)
	}
	var customer store.Customer
	if err := db.Where("code = ?", "C1001").First(&customer).Error; err != nil {
		t.Fatal(err)
	}
	if customer.RiskLevel != "high" || customer.RenewalDate.UTC().Format("2006-01-02") != "2026-10-05" {
		t.Fatalf("unexpected C1001 characteristics: risk=%s renewal=%s", customer.RiskLevel, customer.RenewalDate)
	}
	var order store.WorkOrder
	if err := db.Where("code = ?", "WO-1001").First(&order).Error; err != nil {
		t.Fatal(err)
	}
	if !order.UpdatedAt.UTC().Equal(anchor) {
		t.Fatalf("WO-1001 update time changed across seeding: %s", order.UpdatedAt)
	}
}
