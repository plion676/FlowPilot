package transport

import (
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"opspilot/business-service/internal/store"
)

func decodeConfig(c *gin.Context, value any) bool {
	decoder := json.NewDecoder(http.MaxBytesReader(c.Writer, c.Request.Body, 65536))
	decoder.DisallowUnknownFields()
	return decoder.Decode(value) == nil && decoder.Decode(&struct{}{}) == io.EOF
}

type endpointInput struct {
	ID                string `json:"id"`
	Name              string `json:"name"`
	RoleID            string `json:"role_id"`
	URL               string `json:"url"`
	CredentialProfile string `json:"credential_profile"`
	Enabled           *bool  `json:"enabled"`
	ExpectedVersion   uint64 `json:"expected_version"`
}

func registerEndpointRoutes(internal *gin.RouterGroup, db *gorm.DB) {
	internal.GET("/mcp-endpoints", func(c *gin.Context) {
		endpoints := []store.MCPEndpoint{}
		checks := []store.MCPEndpointCheck{}
		if db.WithContext(c.Request.Context()).Order("id").Find(&endpoints).Error != nil || db.WithContext(c.Request.Context()).Find(&checks).Error != nil {
			fail(c, 503, "DEPENDENCY_UNAVAILABLE", "配置服务暂不可用")
			return
		}
		c.JSON(200, gin.H{"endpoints": endpoints, "checks": checks})
	})
	internal.GET("/mcp-endpoints/:endpoint_id", func(c *gin.Context) {
		var endpoint store.MCPEndpoint
		if err := db.WithContext(c.Request.Context()).First(&endpoint, "id = ?", c.Param("endpoint_id")).Error; err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, endpoint)
	})
	save := func(c *gin.Context) {
		var input endpointInput
		if !decodeConfig(c, &input) {
			fail(c, 400, "INVALID_ARGUMENTS", "连接配置格式无效")
			return
		}
		create := c.Request.Method == "POST"
		id := c.Param("endpoint_id")
		if create {
			id = input.ID
		}
		if !store.EndpointIDPattern.MatchString(id) || (!create && input.ID != "" && input.ID != id) || len([]rune(input.Name)) < 1 || len([]rune(input.Name)) > 120 || !agentIDPattern.MatchString(input.RoleID) || input.Enabled == nil || !store.ValidCredentialProfile(input.CredentialProfile) || !store.ValidEndpointURL(input.URL) {
			fail(c, 400, "INVALID_ENDPOINT_TARGET", "连接字段或允许目标无效")
			return
		}
		var saved store.MCPEndpoint
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var role store.AgentProfile
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&role, "id = ?", input.RoleID).Error; err != nil {
				return err
			}
			beforeJSON := "null"
			if create {
				var count int64
				if err := tx.Model(&store.MCPEndpoint{}).Count(&count).Error; err != nil {
					return err
				}
				if count >= 10 {
					return invalidConfigError
				}
				saved = store.MCPEndpoint{ID: id, Name: input.Name, RoleID: input.RoleID, URL: input.URL, CredentialProfile: input.CredentialProfile, Enabled: *input.Enabled, Version: 1, ExecutionRevision: 1}
				var existing int64
				tx.Model(&store.MCPEndpoint{}).Where("id = ? OR url = ?", id, input.URL).Count(&existing)
				if existing > 0 {
					return conflictError
				}
				if err := tx.Create(&saved).Error; err != nil {
					return err
				}
			} else {
				if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&saved, "id = ?", id).Error; err != nil {
					return err
				}
				if saved.Version != input.ExpectedVersion {
					return conflictError
				}
				var otherRoles int64
				if err := tx.Model(&store.AgentEndpointBinding{}).Where("endpoint_id = ? AND agent_id <> ?", id, input.RoleID).Count(&otherRoles).Error; err != nil {
					return err
				}
				if otherRoles > 0 {
					return invalidConfigError
				}
				var duplicate int64
				if err := tx.Model(&store.MCPEndpoint{}).Where("url = ? AND id <> ?", input.URL, id).Count(&duplicate).Error; err != nil {
					return err
				}
				if duplicate > 0 {
					return conflictError
				}
				encoded, _ := json.Marshal(saved)
				beforeJSON = string(encoded)
				revision := saved.ExecutionRevision
				if saved.RoleID != input.RoleID || saved.URL != input.URL || saved.CredentialProfile != input.CredentialProfile || saved.Enabled != *input.Enabled {
					revision++
				}
				updates := map[string]any{"name": input.Name, "role_id": input.RoleID, "url": input.URL, "credential_profile": input.CredentialProfile, "enabled": *input.Enabled, "version": saved.Version + 1, "execution_revision": revision}
				result := tx.Model(&store.MCPEndpoint{}).Where("id = ? AND version = ?", id, input.ExpectedVersion).Updates(updates)
				if result.Error != nil {
					return result.Error
				}
				if result.RowsAffected != 1 {
					return conflictError
				}
				if err := tx.First(&saved, "id = ?", id).Error; err != nil {
					return err
				}
			}
			afterJSON, _ := json.Marshal(saved)
			return tx.Create(&store.MCPEndpointAudit{ID: uuid.NewString(), EndpointID: id, RequestID: c.GetHeader("X-Request-ID"), ActorID: "local-developer", BeforeJSON: beforeJSON, AfterJSON: string(afterJSON)}).Error
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, saved)
	}
	internal.POST("/mcp-endpoints", save)
	internal.PUT("/mcp-endpoints/:endpoint_id", save)
	internal.GET("/mcp-endpoints/:endpoint_id/check", func(c *gin.Context) {
		var check store.MCPEndpointCheck
		if err := db.WithContext(c.Request.Context()).First(&check, "endpoint_id = ?", c.Param("endpoint_id")).Error; err != nil {
			configFailure(c, err)
			return
		}
		var catalog any
		if json.Unmarshal([]byte(check.CatalogJSON), &catalog) != nil {
			catalog = []any{}
		}
		c.JSON(200, gin.H{"check": check, "tools": catalog})
	})
	internal.PUT("/mcp-endpoints/:endpoint_id/check-result", func(c *gin.Context) {
		var input struct {
			ExecutionRevision uint64           `json:"execution_revision"`
			Status            string           `json:"status"`
			ErrorCode         string           `json:"error_code"`
			Tools             []map[string]any `json:"tools"`
		}
		if !decodeConfig(c, &input) || input.ExecutionRevision == 0 || (input.Status != "ready" && input.Status != "failed") || len(input.ErrorCode) > 80 || len(input.Tools) > 5 {
			fail(c, 400, "INVALID_ARGUMENTS", "检查结果格式无效")
			return
		}
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var endpoint store.MCPEndpoint
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&endpoint, "id = ?", c.Param("endpoint_id")).Error; err != nil {
				return err
			}
			if endpoint.ExecutionRevision != input.ExecutionRevision {
				return conflictError
			}
			if input.Tools == nil {
				input.Tools = []map[string]any{}
			}
			encoded, _ := json.Marshal(input.Tools)
			check := store.MCPEndpointCheck{EndpointID: endpoint.ID, CheckedExecutionRevision: input.ExecutionRevision, Status: input.Status, ErrorCode: input.ErrorCode, CatalogJSON: string(encoded), CheckedAt: time.Now().UTC()}
			return tx.Clauses(clause.OnConflict{UpdateAll: true}).Create(&check).Error
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"saved": true})
	})
}
