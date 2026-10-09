package transport

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"opspilot/business-service/internal/store"
)

func TestTracePersistenceIdentityIdempotencyAndTerminal(t *testing.T) {
	db, router := configDB(t)
	id, requestID, eventID := uuid.NewString(), uuid.NewString(), uuid.NewString()
	identity := `"role":"consultant","actor_id":"demo-consultant"`
	create := fmt.Sprintf(`{%s,"trace_id":"%s","request_id":"%s","summary":"查询 C1001","model":"fake"}`, identity, id, requestID)
	if r := configRequest(router, "POST", "/internal/traces", create); r.Code != 201 {
		t.Fatal(r.Code, r.Body)
	}
	path := "/internal/traces/" + id
	event := fmt.Sprintf(`{%s,"event_id":"%s","kind":"tool_call","step":1,"call_id":"call-1","payload":{"name":"lookup"}}`, identity, eventID)
	for i := 0; i < 2; i++ {
		if r := configRequest(router, "POST", path+"/events", event); r.Code != 201 {
			t.Fatal(r.Code, r.Body)
		}
	}
	var count int64
	db.Model(&store.TraceEvent{}).Count(&count)
	if count != 1 {
		t.Fatal("duplicate event", count)
	}
	if r := configRequest(router, "POST", path+"/events", strings.Replace(event, "lookup", "different", 1)); r.Code != 409 {
		t.Fatal("conflicting retry accepted", r.Code)
	}
	if r := configRequest(router, "GET", path+"?role=consultant&actor_id=another", ""); r.Code != 404 {
		t.Fatal("cross-owner access")
	}
	if r := configRequest(router, "POST", path+"/events", strings.Replace(event, "demo-consultant", "another", 1)); r.Code != 404 {
		t.Fatal("cross-owner write")
	}
	finish := `{` + identity + `,"status":"succeeded","incomplete":false,"error_code":""}`
	if r := configRequest(router, "POST", path+"/finish", finish); r.Code != 200 {
		t.Fatal(r.Code, r.Body)
	}
	if r := configRequest(router, "POST", path+"/events", strings.Replace(event, eventID, uuid.NewString(), 1)); r.Code != 409 {
		t.Fatal("append after finish")
	}
	r := configRequest(router, "GET", path+"?role=consultant&actor_id=demo-consultant", "")
	var body struct {
		Trace  store.TraceRun     `json:"trace"`
		Events []store.TraceEvent `json:"events"`
	}
	if err := json.Unmarshal(r.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Trace.Status != "succeeded" || len(body.Events) != 1 || body.Events[0].Sequence != 1 {
		t.Fatal("saved trace lost")
	}
	if strings.Contains(r.Body.String(), "actor_id") {
		t.Fatal("private identity exposed")
	}
	r = configRequest(router, "GET", path+"?role=consultant&actor_id=demo-consultant&after_sequence=1", "")
	if !strings.Contains(r.Body.String(), `"events":[]`) {
		t.Fatal("incremental events duplicated")
	}
	r = configRequest(router, "GET", "/internal/traces?role=consultant&actor_id=demo-consultant&search="+requestID[:8], "")
	if !strings.Contains(r.Body.String(), id) {
		t.Fatal("request ID search failed")
	}
	if r := configRequest(router, "POST", "/internal/traces", create[:len(create)-1]+`,"approved":true}`); r.Code != 400 {
		t.Fatal("unknown fields accepted")
	}
	if r := configRequest(router, "GET", "/internal/traces?role=consultant&actor_id=demo-consultant&offset=-1", ""); r.Code != 400 {
		t.Fatal("invalid pagination")
	}
}

func TestTraceLimitsPaginationAndInterrupted(t *testing.T) {
	db, router := configDB(t)
	run := store.TraceRun{ID: uuid.NewString(), RequestID: uuid.NewString(), Role: "consultant", ActorID: "demo-consultant", Status: "running"}
	db.Create(&run)
	for i := 0; i < 256; i++ {
		event := store.TraceEvent{EventID: uuid.NewString(), Kind: "user_message", Payload: json.RawMessage(`{"content":"test"}`)}
		if err := store.AppendTraceEvent(t.Context(), db, run.Role, run.ActorID, run.ID, &event); err != nil {
			t.Fatal(err)
		}
	}
	event := store.TraceEvent{EventID: uuid.NewString(), Kind: "user_message", Payload: json.RawMessage(`{}`)}
	if err := store.AppendTraceEvent(t.Context(), db, run.Role, run.ActorID, run.ID, &event); err != store.ErrTraceLimit {
		t.Fatal("unbounded events", err)
	}
	r := configRequest(router, "GET", "/internal/traces/"+run.ID+"?role=consultant&actor_id=demo-consultant", "")
	var body struct {
		Events  []store.TraceEvent `json:"events"`
		HasMore bool               `json:"has_more"`
	}
	json.Unmarshal(r.Body.Bytes(), &body)
	if len(body.Events) != 100 || !body.HasMore {
		t.Fatal("page not bounded")
	}
	db.Model(&run).Update("updated_at", time.Now().UTC().Add(-16*time.Minute))
	r = configRequest(router, "GET", "/internal/traces/"+run.ID+"?role=consultant&actor_id=demo-consultant", "")
	if !strings.Contains(r.Body.String(), `"status":"interrupted"`) || !strings.Contains(r.Body.String(), `"incomplete":true`) {
		t.Fatal("abandoned run remains running", r.Body)
	}
}
