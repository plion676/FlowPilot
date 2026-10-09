package transport

import (
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
	"opspilot/business-service/internal/store"
	"testing"
)

func TestTaskHTTPIdentityStrictInputsAndPublicView(t *testing.T) {
	db, router := configDB(t)
	for _, name := range store.FollowupTools {
		binding := store.AgentToolBinding{AgentID: "consultant", EndpointID: "business", ToolName: name}
		if err := db.Where(binding).FirstOrCreate(&binding).Error; err != nil {
			t.Fatal(err)
		}
	}
	var profile store.AgentProfile
	db.First(&profile, "id = ?", "consultant")
	body := fmt.Sprintf(`{"role":"consultant","actor_id":"demo-consultant","idempotency_key":"%s","endpoint_id":"business","binding_version":%d}`, uuid.NewString(), profile.Version)
	response := configRequest(router, "POST", "/internal/tasks", body)
	if response.Code != 201 {
		t.Fatal(response.Code, response.Body)
	}
	var task map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &task); err != nil {
		t.Fatal(err)
	}
	for _, private := range []string{"lease_token", "lease_hash", "grant", "actor_id", "binding_version"} {
		if _, exists := task[private]; exists {
			t.Fatal("private task data leaked")
		}
	}
	id := task["task_id"].(string)
	if response := configRequest(router, "GET", "/internal/tasks/"+id+"?role=consultant&actor_id=another", ""); response.Code != 404 {
		t.Fatal("cross-user task access")
	}
	if response := configRequest(router, "POST", "/internal/tasks", body[:len(body)-1]+`,"approved":true}`); response.Code != 400 {
		t.Fatal("extra field accepted")
	}
	if response := testRouter(t).get("/internal/tasks?role=consultant&actor_id=demo-consultant", ""); response.Code != 401 {
		t.Fatal("missing service identity accepted")
	}
}
