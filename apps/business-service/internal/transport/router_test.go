package transport

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"opspilot/business-service/internal/seed"
	"opspilot/business-service/internal/store"
)

const testToken = "test-only-service-token-123"

func testRouter(t *testing.T) *ginTestRouter {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Migrate(db); err != nil {
		t.Fatal(err)
	}
	if err := seed.Seed(db, time.Date(2026, 9, 30, 0, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	return &ginTestRouter{handler: NewRouter(db, testToken)}
}

type ginTestRouter struct{ handler http.Handler }

func (router *ginTestRouter) get(path string, token string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if token != "" {
		req.Header.Set("X-Internal-Service-Token", token)
	}
	req.Header.Set("X-Request-ID", "11111111-1111-4111-8111-111111111111")
	response := httptest.NewRecorder()
	router.handler.ServeHTTP(response, req)
	return response
}

func TestInternalReadsRequireServiceIdentity(t *testing.T) {
	router := testRouter(t)
	response := router.get("/internal/crm/customers/C1001", "")
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d", response.Code)
	}
	response = router.get("/internal/crm/customers/C1001", testToken)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body)
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["customer_code"] != "C1001" || payload["risk_level"] != "high" || payload["renewal_date"] != "2026-10-05" {
		t.Fatalf("unexpected response: %v", payload)
	}
}

func TestOnlyOpenTicketsAreReturned(t *testing.T) {
	router := testRouter(t)
	response := router.get("/internal/crm/customers/C1001/tickets?status=open", testToken)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body)
	}
	var payload struct {
		Tickets []struct {
			TicketID string `json:"ticket_id"`
			Status   string `json:"status"`
		} `json:"tickets"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Tickets) != 1 || payload.Tickets[0].TicketID != "T1001" || payload.Tickets[0].Status != "open" {
		t.Fatalf("unexpected tickets: %+v", payload)
	}
	if router.get("/internal/crm/customers/C1001/tickets?status=closed", testToken).Code != http.StatusBadRequest {
		t.Fatal("closed query was not rejected")
	}
}

func TestMesReadAndInvalidIdentifiers(t *testing.T) {
	router := testRouter(t)
	if router.get("/internal/mes/work-orders/WO-1001", testToken).Code != http.StatusOK {
		t.Fatal("MES read failed")
	}
	if router.get("/internal/mes/work-orders/WO-9999", testToken).Code != http.StatusNotFound {
		t.Fatal("missing order not reported")
	}
	if router.get("/internal/crm/customers/invalid", testToken).Code != http.StatusBadRequest {
		t.Fatal("invalid customer code not rejected")
	}
}
