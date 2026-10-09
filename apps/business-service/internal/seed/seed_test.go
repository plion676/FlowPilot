package seed

import (
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

func TestSeedIsDeterministicAndIdempotent(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
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
	var count int64
	if err := db.Model(&store.Customer{}).Count(&count).Error; err != nil || count != 3 {
		t.Fatalf("customers=%d err=%v", count, err)
	}
	if err := db.Model(&store.Ticket{}).Count(&count).Error; err != nil || count != 3 {
		t.Fatalf("tickets=%d err=%v", count, err)
	}
	if err := db.Model(&store.WorkOrder{}).Count(&count).Error; err != nil || count != 2 {
		t.Fatalf("work_orders=%d err=%v", count, err)
	}
	var customer store.Customer
	if err := db.Where("code = ?", "C1001").First(&customer).Error; err != nil {
		t.Fatal(err)
	}
	if customer.RiskLevel != "high" || !customer.RenewalDate.Equal(anchor.AddDate(0, 0, 5)) {
		t.Fatalf("C1001 unexpected: %+v", customer)
	}
	var order store.WorkOrder
	if err := db.Where("code = ?", "WO-1001").First(&order).Error; err != nil {
		t.Fatal(err)
	}
	if !order.UpdatedAt.Equal(anchor) {
		t.Fatalf("WO-1001 update time changed across seeding: %s", order.UpdatedAt)
	}
}

func TestFollowupDemoIsAdditiveAndDateStamped(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Migrate(db); err != nil {
		t.Fatal(err)
	}
	anchor := time.Date(2026, 10, 9, 0, 0, 0, 0, time.UTC)
	if err := Seed(db, anchor.AddDate(0, 0, -9)); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err := AddFollowupDemo(db, anchor); err != nil {
			t.Fatal(err)
		}
	}
	var count int64
	db.Model(&store.Customer{}).Count(&count)
	if count != 4 {
		t.Fatal("additive demo repeated")
	}
	var old store.Customer
	db.First(&old, "code = ?", "C1001")
	if old.RenewalDate.Format("2006-01-02") != "2026-10-05" {
		t.Fatal("old seed overwritten")
	}
}
