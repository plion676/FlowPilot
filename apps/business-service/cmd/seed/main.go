package main

import (
	"log"
	"os"
	"time"

	"opspilot/business-service/internal/seed"
	"opspilot/business-service/internal/store"
)

func main() {
	db, err := store.OpenMySQLFromEnv()
	if err != nil {
		log.Fatal(err)
	}
	if err := store.Migrate(db); err != nil {
		log.Fatal(err)
	}
	anchor := time.Now().UTC()
	if raw := os.Getenv("SEED_DATE"); raw != "" {
		anchor, err = time.Parse("2006-01-02", raw)
		if err != nil {
			log.Fatal("SEED_DATE 必须为 YYYY-MM-DD")
		}
	}
	if err := seed.Seed(db, anchor); err != nil {
		log.Fatal(err)
	}
	log.Printf("模拟数据初始化成功：%s %s", seed.Version, anchor.Format("2006-01-02"))
}
