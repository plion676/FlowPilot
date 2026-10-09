// Read-only verification command. Never returns lease tokens or credentials.
package main

import (
	"encoding/json"
	"github.com/google/uuid"
	"log"
	"opspilot/business-service/internal/store"
	"os"
)

func main() {
	if len(os.Args) != 2 {
		log.Fatal("需要任务 UUID")
	}
	if _, err := uuid.Parse(os.Args[1]); err != nil {
		log.Fatal("任务 UUID 无效")
	}
	db, err := store.OpenMySQLFromEnv()
	if err != nil {
		log.Fatal("无法连接模拟数据库")
	}
	var task store.AgentTask
	if db.First(&task, "id = ?", os.Args[1]).Error != nil {
		log.Fatal("任务不存在")
	}
	var count int64
	if db.Model(&store.FollowupPlan{}).Where("task_id = ?", task.ID).Count(&count).Error != nil {
		log.Fatal("读取计划失败")
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"task_id": task.ID, "status": task.Status, "plan_count": count}); err != nil {
		log.Fatal("输出失败")
	}
}
