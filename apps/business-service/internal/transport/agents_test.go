package transport

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

func configDB(t *testing.T) (*gorm.DB, *gin.Engine) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Migrate(db); err != nil {
		t.Fatal(err)
	}
	return db, NewRouter(db, testToken)
}
func configRequest(router *gin.Engine, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Service-Token", testToken)
	req.Header.Set("X-Request-ID", "11111111-1111-4111-8111-111111111111")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, req)
	return response
}

func TestBindingMigrationPersistenceAndConflict(t *testing.T) {
	db, router := configDB(t)
	response := configRequest(router, "GET", "/internal/agents/consultant/bindings", "")
	var profile agentView
	if err := json.Unmarshal(response.Body.Bytes(), &profile); err != nil {
		t.Fatal(err)
	}
	if profile.Version != 2 || len(profile.Tools) != 2 || profile.Tools[0].EndpointID != "business" {
		t.Fatal(profile)
	}
	body := `{"endpoint_ids":[],"tools":[],"expected_version":2,"expected_endpoint_versions":{}}`
	if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", body); response.Code != 200 {
		t.Fatal(response.Body)
	}
	if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", body); response.Code != 409 {
		t.Fatal(response.Body)
	}
	if err := store.Migrate(db); err != nil {
		t.Fatal(err)
	}
	response = configRequest(router, "GET", "/internal/agents/consultant/bindings", "")
	json.Unmarshal(response.Body.Bytes(), &profile)
	if profile.Version != 3 || len(profile.Tools) != 0 || len(profile.EndpointIDs) != 0 {
		t.Fatal(profile)
	}
	var count int64
	db.Model(&store.AgentBindingAudit{}).Count(&count)
	if count != 2 {
		t.Fatal(count)
	}
	for _, body := range []string{`{"tools":[],"expected_version":3}`, `{"endpoint_ids":[],"tools":[{"endpoint_id":"business","tool_name":"crm.get_customer_overview"}],"expected_version":3}`, `{"endpoint_ids":[],"tools":[],"expected_version":3}{}`} {
		if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", body); response.Code != 400 {
			t.Fatal(response.Code, response.Body)
		}
	}
}

func TestEndpointVersionsSourceIsolationAndChecks(t *testing.T) {
	t.Setenv("MCP_ALLOWED_TARGETS", `["http://127.0.0.1:3100/mcp/consultant","http://127.0.0.1:3100/mcp/consultant_aux"]`)
	db, router := configDB(t)
	body := `{"id":"mes","name":"顾问备用连接","role_id":"consultant","url":"http://127.0.0.1:3100/mcp/consultant_aux","credential_profile":"local","enabled":true}`
	response := configRequest(router, "POST", "/internal/mcp-endpoints", body)
	if response.Code != 200 {
		t.Fatal(response.Body)
	}
	bad := `{"expected_version":2,"endpoint_ids":["mes"],"expected_endpoint_versions":{"mes":1},"tools":[{"endpoint_id":"mes","tool_name":"mes.get_work_order_status"}]}`
	if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", bad); response.Code != 400 {
		t.Fatal(response.Body)
	}
	check := `{"execution_revision":1,"status":"ready","error_code":"","tools":[{"name":"mes.get_work_order_status","bindable":true}]}`
	if response := configRequest(router, "PUT", "/internal/mcp-endpoints/mes/check-result", check); response.Code != 200 {
		t.Fatal(response.Body)
	}
	if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", bad); response.Code != 200 {
		t.Fatal(response.Body)
	}
	auth := configRequest(router, "GET", "/internal/agents/consultant/authorization?endpoint_id=business", "")
	if auth.Code != 400 {
		t.Fatal(auth.Body)
	}
	auth = configRequest(router, "GET", "/internal/agents/consultant/authorization?endpoint_id=mes", "")
	if !strings.Contains(auth.Body.String(), "mes.get_work_order_status") {
		t.Fatal(auth.Body)
	}
	update := `{"name":"顾问备用连接2","role_id":"consultant","url":"http://127.0.0.1:3100/mcp/consultant_aux","credential_profile":"local","enabled":true,"expected_version":1}`
	response = configRequest(router, "PUT", "/internal/mcp-endpoints/mes", update)
	var endpoint store.MCPEndpoint
	json.Unmarshal(response.Body.Bytes(), &endpoint)
	if endpoint.Version != 2 || endpoint.ExecutionRevision != 1 {
		t.Fatal(endpoint, response.Body)
	}
	if response := configRequest(router, "PUT", "/internal/mcp-endpoints/mes", update); response.Code != 409 {
		t.Fatal(response.Body)
	}
	update = strings.Replace(update, `"expected_version":1`, `"expected_version":2`, 1)
	update = strings.Replace(update, `"enabled":true`, `"enabled":false`, 1)
	response = configRequest(router, "PUT", "/internal/mcp-endpoints/mes", update)
	json.Unmarshal(response.Body.Bytes(), &endpoint)
	if endpoint.Enabled || endpoint.ExecutionRevision != 2 {
		t.Fatal(endpoint)
	}
	if response := configRequest(router, "PUT", "/internal/mcp-endpoints/mes/check-result", check); response.Code != 409 {
		t.Fatal(response.Body)
	}
	var count int64
	db.Model(&store.MCPEndpointAudit{}).Count(&count)
	if count != 3 {
		t.Fatal(count)
	}
}

func TestInvalidTargetsAndUnknownFields(t *testing.T) {
	_, router := configDB(t)
	for _, url := range []string{"http://169.254.169.254/latest", "http://localhost:3100/mcp", "http://127.0.0.1:3100/mcp?key=x", "http://user:pass@127.0.0.1:3100/mcp", "http://127.0.0.1:3307/mcp"} {
		encoded, _ := json.Marshal(map[string]any{"id": "bad", "name": "bad", "role_id": "consultant", "url": url, "credential_profile": "local", "enabled": true})
		if response := configRequest(router, "POST", "/internal/mcp-endpoints", string(encoded)); response.Code != 400 {
			t.Fatal(url, response.Body)
		}
	}
}

func TestTwoRolesHaveSeparateEndpointAuthority(t *testing.T) {
	t.Setenv("MCP_ALLOWED_TARGETS", `["http://127.0.0.1:3100/mcp/consultant","http://127.0.0.1:3100/mcp/production"]`)
	db, router := configDB(t)
	if err := db.Create(&store.AgentProfile{ID: "production", Name: "生产查询", Version: 1, BindingSchemaVersion: 2, BoundToolsJSON: "[]"}).Error; err != nil {
		t.Fatal(err)
	}
	body := `{"id":"mes","name":"生产角色","role_id":"production","url":"http://127.0.0.1:3100/mcp/production","credential_profile":"local","enabled":true}`
	if response := configRequest(router, "POST", "/internal/mcp-endpoints", body); response.Code != 200 {
		t.Fatal(response.Body)
	}
	check := `{"execution_revision":1,"status":"ready","error_code":"","tools":[{"name":"mes.get_work_order_status","bindable":true}]}`
	if response := configRequest(router, "PUT", "/internal/mcp-endpoints/mes/check-result", check); response.Code != 200 {
		t.Fatal(response.Body)
	}
	selection := `{"expected_version":1,"endpoint_ids":["mes"],"expected_endpoint_versions":{"mes":1},"tools":[{"endpoint_id":"mes","tool_name":"mes.get_work_order_status"}]}`
	if response := configRequest(router, "PUT", "/internal/agents/production/bindings", selection); response.Code != 200 {
		t.Fatal(response.Body)
	}
	for _, path := range []string{"/internal/agents/production/authorization?endpoint_id=business", "/internal/agents/consultant/authorization?endpoint_id=mes"} {
		if response := configRequest(router, "GET", path, ""); response.Code != 400 {
			t.Fatal(response.Body)
		}
	}
	if response := configRequest(router, "GET", "/internal/agents/production/authorization?endpoint_id=mes", ""); response.Code != 200 {
		t.Fatal(response.Body)
	}
	// An unbound source is forbidden even with zero tools and a valid catalog.
	selection = `{"expected_version":2,"endpoint_ids":["mes"],"expected_endpoint_versions":{"mes":1},"tools":[]}`
	if response := configRequest(router, "PUT", "/internal/agents/consultant/bindings", selection); response.Code != 400 {
		t.Fatal(response.Code, response.Body)
	}
	// Cannot reassign a connection while its existing owner still binds it.
	update := `{"name":"生产角色","role_id":"consultant","url":"http://127.0.0.1:3100/mcp/production","credential_profile":"local","enabled":true,"expected_version":1}`
	if response := configRequest(router, "PUT", "/internal/mcp-endpoints/mes", update); response.Code != 400 {
		t.Fatal(response.Code, response.Body)
	}
}
