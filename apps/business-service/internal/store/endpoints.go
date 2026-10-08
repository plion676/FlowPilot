package store

import (
	"encoding/json"
	"errors"
	"net"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var EndpointIDPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}$`)
var ToolNames = map[string]bool{"crm.get_customer_overview": true, "crm.list_open_tickets": true, "mes.get_work_order_status": true, "knowledge.search_sop": true, "workflow.create_followup_plan": true}

func DefaultMCPURL() string {
	if value := os.Getenv("MCP_URL"); value != "" {
		return value
	}
	return "http://127.0.0.1:3100/mcp/consultant"
}

// First slice deliberately accepts literal IPs only: no save-time DNS checks.
func ValidEndpointURL(raw string) bool {
	parsed, err := url.Parse(raw)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || parsed.RawPath != "" || parsed.Opaque != "" || strings.Contains(raw, "%") {
		return false
	}
	ip := net.ParseIP(parsed.Hostname())
	if port := parsed.Port(); port != "" {
		value, err := strconv.Atoi(port)
		if err != nil || value < 1 || value > 65535 {
			return false
		}
	}
	if ip == nil || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || !(ip.IsLoopback() || ip.IsPrivate()) || parsed.Path == "" || strings.Contains(parsed.Path, "..") || strings.Contains(parsed.Path, "//") || strings.Contains(parsed.Path, "\\") {
		return false
	}
	targets := []string{DefaultMCPURL(), "http://127.0.0.1:3100/mcp/consultant"}
	if configured := os.Getenv("MCP_ALLOWED_TARGETS"); configured != "" {
		if json.Unmarshal([]byte(configured), &targets) != nil {
			return false
		}
	}
	for _, target := range targets {
		if raw == target && parsed.String() == raw {
			return true
		}
	}
	return false
}

func ValidCredentialProfile(name string) bool {
	if !EndpointIDPattern.MatchString(name) {
		return false
	}
	configured := os.Getenv("MCP_CREDENTIAL_PROFILES")
	if configured == "" {
		return name == "local"
	}
	var profiles map[string]json.RawMessage
	if json.Unmarshal([]byte(configured), &profiles) != nil {
		return false
	}
	_, ok := profiles[name]
	return ok
}

func MigrateEndpointBindings(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var marker ConfigMigration
		err := tx.First(&marker, "id = ?", "mcp-endpoint-bindings-v2").Error
		if err == nil {
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if !ValidEndpointURL(DefaultMCPURL()) {
			return errors.New("MCP 默认连接不在允许的 IP 目标范围内")
		}
		endpoint := MCPEndpoint{ID: "business", Name: "运营顾问 MCP", RoleID: "consultant", URL: DefaultMCPURL(), CredentialProfile: "local", Enabled: true, Version: 1, ExecutionRevision: 1}
		if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&endpoint).Error; err != nil {
			return err
		}
		var profiles []AgentProfile
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Find(&profiles).Error; err != nil {
			return err
		}
		for _, profile := range profiles {
			if profile.BindingSchemaVersion == 2 {
				continue
			}
			var names []string
			if json.Unmarshal([]byte(profile.BoundToolsJSON), &names) != nil || names == nil {
				return errors.New("旧绑定损坏，停止迁移")
			}
			seen := map[string]bool{}
			if err := tx.Create(&AgentEndpointBinding{AgentID: profile.ID, EndpointID: "business"}).Error; err != nil {
				return err
			}
			tools := []AgentToolBinding{}
			for _, name := range names {
				if !ToolNames[name] || seen[name] {
					return errors.New("旧绑定含非法或重复工具")
				}
				seen[name] = true
				tool := AgentToolBinding{AgentID: profile.ID, EndpointID: "business", ToolName: name}
				if err := tx.Create(&tool).Error; err != nil {
					return err
				}
				tools = append(tools, tool)
			}
			after, _ := json.Marshal(map[string]any{"endpoint_ids": []string{"business"}, "tools": tools})
			previousVersion := profile.Version
			if err := tx.Model(&profile).Updates(map[string]any{"binding_schema_version": 2, "version": previousVersion + 1}).Error; err != nil {
				return err
			}
			if err := tx.Create(&AgentBindingAudit{ID: uuid.NewString(), AgentID: profile.ID, RequestID: uuid.NewString(), ActorID: "configuration-migration", PreviousVersion: previousVersion, Version: previousVersion + 1, BeforeJSON: profile.BoundToolsJSON, AfterJSON: string(after), FormatVersion: 2}).Error; err != nil {
				return err
			}
		}
		return tx.Create(&ConfigMigration{ID: "mcp-endpoint-bindings-v2", CreatedAt: time.Now().UTC()}).Error
	})
}

// Upgrade existing registrations without resetting manual Tool selections.
// Ambiguous/unassigned connections are quarantined until an operator assigns a role.
func MigrateRoleEndpoints(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var marker ConfigMigration
		err := tx.First(&marker, "id = ?", "mcp-role-endpoints-v3").Error
		if err == nil {
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var endpoints []MCPEndpoint
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Find(&endpoints).Error; err != nil {
			return err
		}
		for _, endpoint := range endpoints {
			var sources []AgentEndpointBinding
			if err := tx.Where("endpoint_id = ?", endpoint.ID).Find(&sources).Error; err != nil {
				return err
			}
			consistentOwner := endpoint.RoleID != ""
			for _, source := range sources {
				if source.AgentID != endpoint.RoleID {
					consistentOwner = false
				}
			}
			if consistentOwner {
				continue
			}
			before, _ := json.Marshal(endpoint)
			updates := map[string]any{"role_id": "", "version": endpoint.Version + 1, "execution_revision": endpoint.ExecutionRevision + 1, "enabled": false}
			if len(sources) == 1 {
				updates["role_id"] = sources[0].AgentID
				// Only the shipped default is renamed automatically. Custom targets
				// require a matching role-scoped deployment before re-enabling.
				if endpoint.ID == "business" && sources[0].AgentID == "consultant" && endpoint.URL == "http://127.0.0.1:3100/mcp" && ValidEndpointURL("http://127.0.0.1:3100/mcp/consultant") {
					updates["url"] = "http://127.0.0.1:3100/mcp/consultant"
					if endpoint.Name == "模拟综合业务" {
						updates["name"] = "运营顾问 MCP"
					}
					updates["enabled"] = endpoint.Enabled
				}
			}
			if err := tx.Model(&MCPEndpoint{}).Where("id = ?", endpoint.ID).Updates(updates).Error; err != nil {
				return err
			}
			var saved MCPEndpoint
			if err := tx.First(&saved, "id = ?", endpoint.ID).Error; err != nil {
				return err
			}
			after, _ := json.Marshal(saved)
			if err := tx.Create(&MCPEndpointAudit{ID: uuid.NewString(), EndpointID: endpoint.ID, RequestID: uuid.NewString(), ActorID: "role-endpoint-migration", BeforeJSON: string(before), AfterJSON: string(after)}).Error; err != nil {
				return err
			}
		}
		return tx.Create(&ConfigMigration{ID: "mcp-role-endpoints-v3", CreatedAt: time.Now().UTC()}).Error
	})
}
