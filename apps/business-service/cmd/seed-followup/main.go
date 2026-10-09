package main

import (
	"log"
	"opspilot/business-service/internal/seed"
	"opspilot/business-service/internal/store"
	"time"
)

func main() {
	db, err := store.OpenMySQLFromEnv()
	if err != nil {
		log.Fatal("无法连接模拟数据库")
	}
	if err := store.Migrate(db); err != nil {
		log.Fatal("迁移失败")
	}
	if err := seed.AddFollowupDemo(db, time.Now()); err != nil {
		log.Fatal("新增模拟回访数据失败")
	}
	log.Print("已按日期追加模拟回访客户与工单，未覆盖既有数据")
}
