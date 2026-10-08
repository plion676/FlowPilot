package store

import (
	"sync"
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestToolSourceIsCompositeBelongsTo(t *testing.T) {
	parsed, err := schema.Parse(&AgentToolBinding{}, &sync.Map{}, schema.NamingStrategy{})
	if err != nil {
		t.Fatal(err)
	}
	relation := parsed.Relationships.Relations["Source"]
	if relation.Type != schema.BelongsTo || len(relation.References) != 2 {
		t.Fatal("复合外键不能反向引用工具表")
	}
	for _, reference := range relation.References {
		if reference.PrimaryKey.Schema.Table != "agent_endpoint_bindings" {
			t.Fatal(reference.PrimaryKey.Schema.Table)
		}
	}
}

func TestMigrationPreservesLegacyEmptySelection(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&AgentProfile{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&AgentProfile{ID: "consultant", Name: "人工配置", Version: 7, BoundToolsJSON: "[]"}).Error; err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := Migrate(db); err != nil {
			t.Fatal(err)
		}
	}
	var profile AgentProfile
	db.First(&profile, "id = ?", "consultant")
	if profile.Name != "人工配置" || profile.Version != 8 || profile.BindingSchemaVersion != 2 || profile.BoundToolsJSON != "[]" {
		t.Fatal(profile)
	}
	var count int64
	db.Model(&AgentToolBinding{}).Where("agent_id = ?", "consultant").Count(&count)
	if count != 0 {
		t.Fatal(count)
	}
}

func TestRoleEndpointMigrationPreservesSelectionsAndQuarantinesLegacy(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := Migrate(db); err != nil {
		t.Fatal(err)
	}
	// Recreate an existing v2 registration; never read the obsolete JSON as grants.
	db.Where("id = ?", "mcp-role-endpoints-v3").Delete(&ConfigMigration{})
	db.Model(&MCPEndpoint{}).Where("id = ?", "business").Updates(map[string]any{"role_id": "", "url": "http://127.0.0.1:3100/mcp", "name": "人工名称"})
	db.Create(&MCPEndpoint{ID: "crm", Name: "旧领域连接", Category: "CRM", URL: "http://127.0.0.1:3100/mcp/crm", Enabled: true, CredentialProfile: "local", Version: 4, ExecutionRevision: 4})
	var before AgentProfile
	db.First(&before, "id = ?", "consultant")
	for i := 0; i < 2; i++ {
		if err := MigrateRoleEndpoints(db); err != nil {
			t.Fatal(err)
		}
	}
	var endpoint, legacy MCPEndpoint
	db.First(&endpoint, "id = ?", "business")
	db.First(&legacy, "id = ?", "crm")
	if endpoint.RoleID != "consultant" || endpoint.URL != "http://127.0.0.1:3100/mcp/consultant" || !endpoint.Enabled || endpoint.Name != "人工名称" || endpoint.Version != 2 || endpoint.ExecutionRevision != 2 {
		t.Fatal(endpoint)
	}
	if legacy.Enabled || legacy.RoleID != "" || legacy.Version != 5 || legacy.Category != "CRM" {
		t.Fatal(legacy)
	}
	var after AgentProfile
	db.First(&after, "id = ?", "consultant")
	var tools []AgentToolBinding
	db.Where("agent_id = ?", "consultant").Find(&tools)
	if before.Version != after.Version || len(tools) != 2 {
		t.Fatal(after, tools)
	}
	var audits int64
	db.Model(&MCPEndpointAudit{}).Count(&audits)
	if audits != 2 {
		t.Fatal(audits)
	}
}

func TestRoleMigrationQuarantinesSharedLegacyEndpoint(t *testing.T) {
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := Migrate(db); err != nil {
		t.Fatal(err)
	}
	db.Where("id = ?", "mcp-role-endpoints-v3").Delete(&ConfigMigration{})
	db.Create(&AgentProfile{ID: "support", Name: "测试角色", BoundToolsJSON: "[]", Version: 1, BindingSchemaVersion: 2})
	db.Create(&AgentEndpointBinding{AgentID: "support", EndpointID: "business"})
	if err := MigrateRoleEndpoints(db); err != nil {
		t.Fatal(err)
	}
	var endpoint MCPEndpoint
	db.First(&endpoint, "id = ?", "business")
	var count int64
	db.Model(&AgentEndpointBinding{}).Where("endpoint_id = ?", "business").Count(&count)
	if endpoint.Enabled || endpoint.RoleID != "" || count != 2 {
		t.Fatal(endpoint, count)
	}
}
