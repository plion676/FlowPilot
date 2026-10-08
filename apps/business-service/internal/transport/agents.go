package transport

import (
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"sort"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"opspilot/business-service/internal/store"
)

var agentIDPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,79}$`)
var conflictError = errors.New("configuration conflict")
var invalidConfigError = errors.New("invalid configuration")

type agentView struct {
	ID                   string                   `json:"id"`
	Name                 string                   `json:"name"`
	Version              uint64                   `json:"version"`
	BindingSchemaVersion uint64                   `json:"binding_schema_version"`
	EndpointIDs          []string                 `json:"endpoint_ids"`
	Tools                []store.AgentToolBinding `json:"tools"`
	Endpoints            []store.MCPEndpoint      `json:"endpoints"`
}

func viewAgent(db *gorm.DB, profile store.AgentProfile) (agentView, error) {
	view := agentView{ID: profile.ID, Name: profile.Name, Version: profile.Version, BindingSchemaVersion: profile.BindingSchemaVersion, EndpointIDs: []string{}, Tools: []store.AgentToolBinding{}, Endpoints: []store.MCPEndpoint{}}
	if profile.BindingSchemaVersion != 2 {
		return view, invalidConfigError
	}
	var sources []store.AgentEndpointBinding
	if err := db.Where("agent_id = ?", profile.ID).Order("endpoint_id").Find(&sources).Error; err != nil {
		return view, err
	}
	for _, source := range sources {
		view.EndpointIDs = append(view.EndpointIDs, source.EndpointID)
	}
	if err := db.Where("agent_id = ?", profile.ID).Order("endpoint_id, tool_name").Find(&view.Tools).Error; err != nil {
		return view, err
	}
	if len(view.EndpointIDs) > 0 {
		if err := db.Where("id IN ?", view.EndpointIDs).Order("id").Find(&view.Endpoints).Error; err != nil {
			return view, err
		}
	}
	if len(view.EndpointIDs) != len(view.Endpoints) {
		return view, invalidConfigError
	}
	return view, nil
}

func registerAgentRoutes(internal *gin.RouterGroup, db *gorm.DB) {
	internal.GET("/agents", func(c *gin.Context) {
		items := []agentView{}
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var profiles []store.AgentProfile
			if err := tx.Order("id").Find(&profiles).Error; err != nil {
				return err
			}
			for _, profile := range profiles {
				view, err := viewAgent(tx, profile)
				if err != nil {
					return err
				}
				items = append(items, view)
			}
			return nil
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"agents": items})
	})
	internal.GET("/agents/:agent_id/bindings", func(c *gin.Context) {
		var view agentView
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var profile store.AgentProfile
			if err := tx.First(&profile, "id = ?", c.Param("agent_id")).Error; err != nil {
				return err
			}
			var err error
			view, err = viewAgent(tx, profile)
			return err
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, view)
	})
	internal.GET("/agents/:agent_id/authorization", func(c *gin.Context) {
		var view agentView
		var endpoint store.MCPEndpoint
		names := []string{}
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var profile store.AgentProfile
			if err := tx.First(&profile, "id = ?", c.Param("agent_id")).Error; err != nil {
				return err
			}
			var err error
			view, err = viewAgent(tx, profile)
			if err != nil {
				return err
			}
			found := false
			for _, ep := range view.Endpoints {
				if ep.ID == c.Query("endpoint_id") {
					endpoint = ep
					found = true
				}
			}
			if !found || endpoint.RoleID != profile.ID {
				return invalidConfigError
			}
			for _, tool := range view.Tools {
				if tool.EndpointID == endpoint.ID {
					names = append(names, tool.ToolName)
				}
			}
			return nil
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, gin.H{"id": view.ID, "version": view.Version, "endpoint_id": endpoint.ID, "endpoint_revision": endpoint.ExecutionRevision, "enabled": endpoint.Enabled, "bound_tools": names})
	})
	internal.PUT("/agents/:agent_id/bindings", func(c *gin.Context) {
		var input struct {
			ExpectedVersion          uint64                    `json:"expected_version"`
			EndpointIDs              *[]string                 `json:"endpoint_ids"`
			ExpectedEndpointVersions map[string]uint64         `json:"expected_endpoint_versions"`
			Tools                    *[]store.AgentToolBinding `json:"tools"`
		}
		if !agentIDPattern.MatchString(c.Param("agent_id")) || !decodeConfig(c, &input) || input.ExpectedVersion == 0 || input.EndpointIDs == nil || input.Tools == nil || len(*input.EndpointIDs) > 10 || len(*input.Tools) > 50 {
			fail(c, 400, "INVALID_ARGUMENTS", "能力配置格式无效")
			return
		}
		selected := map[string]bool{}
		toolKeys := map[string]bool{}
		for _, id := range *input.EndpointIDs {
			if !store.EndpointIDPattern.MatchString(id) || selected[id] {
				fail(c, 400, "INVALID_ARGUMENTS", "连接不存在或重复")
				return
			}
			selected[id] = true
		}
		for _, tool := range *input.Tools {
			key := tool.EndpointID + "/" + tool.ToolName
			if !selected[tool.EndpointID] || !store.ToolNames[tool.ToolName] || toolKeys[key] {
				fail(c, 400, "INVALID_ARGUMENTS", "工具来源无效或重复")
				return
			}
			toolKeys[key] = true
		}
		sort.Strings(*input.EndpointIDs)
		var updated agentView
		err := db.WithContext(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
			var profile store.AgentProfile
			if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&profile, "id = ?", c.Param("agent_id")).Error; err != nil {
				return err
			}
			if profile.Version != input.ExpectedVersion {
				return conflictError
			}
			before, err := viewAgent(tx, profile)
			if err != nil {
				return err
			}
			oldTools := map[string]bool{}
			for _, tool := range before.Tools {
				oldTools[tool.EndpointID+"/"+tool.ToolName] = true
			}
			for _, id := range *input.EndpointIDs {
				var endpoint store.MCPEndpoint
				if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&endpoint, "id = ?", id).Error; err != nil {
					return err
				}
				if endpoint.Version != input.ExpectedEndpointVersions[id] {
					return conflictError
				}
				if endpoint.RoleID != profile.ID {
					return invalidConfigError
				}
				for _, tool := range *input.Tools {
					if tool.EndpointID != id || oldTools[id+"/"+tool.ToolName] {
						continue
					}
					var check store.MCPEndpointCheck
					if !endpoint.Enabled || tx.First(&check, "endpoint_id = ?", id).Error != nil || check.Status != "ready" || check.CheckedExecutionRevision != endpoint.ExecutionRevision {
						return invalidConfigError
					}
					var catalog []struct {
						Name     string `json:"name"`
						Bindable bool   `json:"bindable"`
					}
					if json.Unmarshal([]byte(check.CatalogJSON), &catalog) != nil {
						return invalidConfigError
					}
					found := false
					for _, item := range catalog {
						if item.Name == tool.ToolName && item.Bindable {
							found = true
						}
					}
					if !found {
						return invalidConfigError
					}
				}
			}
			result := tx.Model(&store.AgentProfile{}).Where("id = ? AND version = ?", profile.ID, input.ExpectedVersion).Update("version", input.ExpectedVersion+1)
			if result.Error != nil {
				return result.Error
			}
			if result.RowsAffected != 1 {
				return conflictError
			}
			if err := tx.Where("agent_id = ?", profile.ID).Delete(&store.AgentToolBinding{}).Error; err != nil {
				return err
			}
			if err := tx.Where("agent_id = ?", profile.ID).Delete(&store.AgentEndpointBinding{}).Error; err != nil {
				return err
			}
			for _, id := range *input.EndpointIDs {
				if err := tx.Create(&store.AgentEndpointBinding{AgentID: profile.ID, EndpointID: id}).Error; err != nil {
					return err
				}
			}
			for _, tool := range *input.Tools {
				tool.AgentID = profile.ID
				if err := tx.Create(&tool).Error; err != nil {
					return err
				}
			}
			profile.Version = input.ExpectedVersion + 1
			updated, err = viewAgent(tx, profile)
			if err != nil {
				return err
			}
			beforeJSON, _ := json.Marshal(before)
			afterJSON, _ := json.Marshal(updated)
			return tx.Create(&store.AgentBindingAudit{ID: uuid.NewString(), AgentID: profile.ID, RequestID: c.GetHeader("X-Request-ID"), ActorID: "local-developer", PreviousVersion: input.ExpectedVersion, Version: profile.Version, BeforeJSON: string(beforeJSON), AfterJSON: string(afterJSON), FormatVersion: 2}).Error
		})
		if err != nil {
			configFailure(c, err)
			return
		}
		c.JSON(200, updated)
	})
}

func configFailure(c *gin.Context, err error) {
	switch {
	case errors.Is(err, conflictError):
		fail(c, 409, "BINDING_CONFLICT", "配置已变化，请刷新")
	case errors.Is(err, invalidConfigError):
		fail(c, 400, "INVALID_BINDING", "工具来源未验证或配置无效")
	case errors.Is(err, gorm.ErrRecordNotFound):
		fail(c, 404, "NOT_FOUND", "角色或连接不存在")
	default:
		fail(c, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "配置服务暂不可用")
	}
}
