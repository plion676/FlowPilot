package store

import "time"

// AgentProfile stores manually selected capabilities, separately from CRM data.
type AgentProfile struct {
	ID                   string `gorm:"type:varchar(80);primaryKey"`
	Name                 string `gorm:"type:varchar(120);not null"`
	BoundToolsJSON       string `gorm:"type:json;not null"`
	Version              uint64 `gorm:"not null"`
	BindingSchemaVersion uint64 `gorm:"not null;default:0"`
	UpdatedAt            time.Time
}

type AgentBindingAudit struct {
	ID              string `gorm:"type:varchar(36);primaryKey"`
	AgentID         string `gorm:"type:varchar(80);index;not null"`
	RequestID       string `gorm:"type:varchar(36);not null"`
	ActorID         string `gorm:"type:varchar(80);not null"`
	PreviousVersion uint64
	Version         uint64
	BeforeJSON      string `gorm:"type:json;not null"`
	AfterJSON       string `gorm:"type:json;not null"`
	CreatedAt       time.Time
	FormatVersion   uint64 `gorm:"not null;default:1"`
}

type MCPEndpoint struct {
	ID                string    `gorm:"type:varchar(32);primaryKey" json:"id"`
	Name              string    `gorm:"type:varchar(120);not null" json:"name"`
	Category          string    `gorm:"type:varchar(40);not null" json:"-"` // Legacy data retained for recovery only.
	RoleID            string    `gorm:"type:varchar(80);not null;default:'';index" json:"role_id"`
	URL               string    `gorm:"type:varchar(500);uniqueIndex;not null" json:"url"`
	CredentialProfile string    `gorm:"type:varchar(32);not null" json:"credential_profile"`
	Enabled           bool      `json:"enabled"`
	Version           uint64    `json:"version"`
	ExecutionRevision uint64    `json:"execution_revision"`
	UpdatedAt         time.Time `json:"updated_at"`
}

type AgentEndpointBinding struct {
	AgentID    string       `gorm:"type:varchar(80);primaryKey" json:"-"`
	EndpointID string       `gorm:"type:varchar(32);primaryKey" json:"endpoint_id"`
	Agent      AgentProfile `gorm:"foreignKey:AgentID;references:ID;constraint:OnDelete:RESTRICT" json:"-"`
	Endpoint   MCPEndpoint  `gorm:"foreignKey:EndpointID;references:ID;constraint:OnDelete:RESTRICT" json:"-"`
}

type AgentToolBinding struct {
	AgentID    string               `gorm:"type:varchar(80);primaryKey" json:"-"`
	EndpointID string               `gorm:"type:varchar(32);primaryKey" json:"endpoint_id"`
	ToolName   string               `gorm:"type:varchar(100);primaryKey" json:"tool_name"`
	Source     AgentEndpointBinding `gorm:"belongsTo:Source;foreignKey:AgentID,EndpointID;references:AgentID,EndpointID;constraint:OnDelete:RESTRICT" json:"-"`
}

type MCPEndpointCheck struct {
	EndpointID               string    `gorm:"type:varchar(32);primaryKey" json:"endpoint_id"`
	CheckedExecutionRevision uint64    `json:"checked_execution_revision"`
	Status                   string    `gorm:"type:varchar(40)" json:"status"`
	ErrorCode                string    `gorm:"type:varchar(80)" json:"error_code"`
	CatalogJSON              string    `gorm:"type:json" json:"-"`
	CheckedAt                time.Time `json:"checked_at"`
}

type MCPEndpointAudit struct {
	ID         string `gorm:"type:varchar(36);primaryKey"`
	EndpointID string `gorm:"type:varchar(32);index"`
	RequestID  string `gorm:"type:varchar(36)"`
	ActorID    string `gorm:"type:varchar(80)"`
	BeforeJSON string `gorm:"type:json"`
	AfterJSON  string `gorm:"type:json"`
	CreatedAt  time.Time
}

type ConfigMigration struct {
	ID        string `gorm:"type:varchar(80);primaryKey"`
	CreatedAt time.Time
}

type Customer struct {
	ID          string    `gorm:"type:varchar(36);primaryKey"`
	Code        string    `gorm:"type:varchar(32);uniqueIndex;not null"`
	Name        string    `gorm:"type:varchar(120);not null"`
	RenewalDate time.Time `gorm:"type:date;not null"`
	RiskLevel   string    `gorm:"type:varchar(16);not null"`
	Status      string    `gorm:"type:varchar(16);not null"`
}

type Ticket struct {
	ID         string    `gorm:"type:varchar(36);primaryKey"`
	Code       string    `gorm:"type:varchar(32);uniqueIndex;not null"`
	CustomerID string    `gorm:"type:varchar(36);index;not null"`
	Title      string    `gorm:"type:varchar(160);not null"`
	Status     string    `gorm:"type:varchar(16);not null"`
	Severity   string    `gorm:"type:varchar(16);not null"`
	OpenedAt   time.Time `gorm:"not null"`
}

type WorkOrder struct {
	ID                  string    `gorm:"type:varchar(36);primaryKey"`
	Code                string    `gorm:"type:varchar(32);uniqueIndex;not null"`
	CustomerID          string    `gorm:"type:varchar(36);index;not null"`
	Status              string    `gorm:"type:varchar(32);not null"`
	PlannedDeliveryDate time.Time `gorm:"type:date;not null"`
	UpdatedAt           time.Time
}

type FollowupPlanDraft struct {
	ID          string    `gorm:"type:varchar(36);primaryKey"`
	TaskID      string    `gorm:"type:varchar(36);uniqueIndex;not null"`
	PayloadJSON string    `gorm:"type:json;not null"`
	Status      string    `gorm:"type:varchar(16);not null"`
	ExpiresAt   time.Time `gorm:"not null"`
}

type FollowupPlan struct {
	ID         string    `gorm:"type:varchar(36);primaryKey"`
	TaskID     string    `gorm:"type:varchar(36);uniqueIndex:idx_plan_task_customer;not null"`
	CustomerID string    `gorm:"type:varchar(36);uniqueIndex:idx_plan_task_customer;not null"`
	Action     string    `gorm:"type:varchar(160);not null"`
	DueDate    time.Time `gorm:"type:date;not null"`
	CreatedAt  time.Time
}
