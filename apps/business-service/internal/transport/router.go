package transport

import (
	"crypto/subtle"
	"errors"
	"net/http"
	"regexp"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"opspilot/business-service/internal/store"
)

var customerCodePattern = regexp.MustCompile(`^C[0-9]{4,}$`)
var workOrderCodePattern = regexp.MustCompile(`^WO-[0-9]{4,}$`)

func NewRouter(db *gorm.DB, serviceToken string) *gin.Engine {
	router := gin.New()
	router.Use(gin.Recovery())
	router.GET("/health", func(c *gin.Context) {
		sqlDB, err := db.DB()
		if err != nil || sqlDB.PingContext(c.Request.Context()) != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"status": "unavailable"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	internal := router.Group("/internal")
	internal.Use(func(c *gin.Context) {
		provided := c.GetHeader("X-Internal-Service-Token")
		if serviceToken == "" || subtle.ConstantTimeCompare([]byte(provided), []byte(serviceToken)) != 1 {
			fail(c, http.StatusUnauthorized, "UNAUTHORIZED", "内部调用身份无效")
			c.Abort()
			return
		}
		if _, err := uuid.Parse(c.GetHeader("X-Request-ID")); err != nil {
			fail(c, http.StatusBadRequest, "INVALID_REQUEST_ID", "请求标识格式无效")
			c.Abort()
		}
	})
	registerAgentRoutes(internal, db)
	registerEndpointRoutes(internal, db)
	internal.GET("/crm/customers/:customer_code", func(c *gin.Context) {
		code := c.Param("customer_code")
		if !customerCodePattern.MatchString(code) {
			fail(c, http.StatusBadRequest, "INVALID_ARGUMENTS", "客户标识格式无效")
			return
		}
		var customer store.Customer
		result := db.WithContext(c.Request.Context()).Where("code = ?", code).First(&customer)
		if errors.Is(result.Error, gorm.ErrRecordNotFound) {
			fail(c, http.StatusNotFound, "NOT_FOUND", "客户不存在")
			return
		}
		if result.Error != nil {
			fail(c, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "数据服务暂不可用")
			return
		}
		c.JSON(http.StatusOK, gin.H{
			"customer_code": customer.Code,
			"name":          customer.Name,
			"renewal_date":  customer.RenewalDate.UTC().Format("2006-01-02"),
			"risk_level":    customer.RiskLevel,
		})
	})
	internal.GET("/crm/customers/:customer_code/tickets", func(c *gin.Context) {
		code := c.Param("customer_code")
		if !customerCodePattern.MatchString(code) || c.Query("status") != "open" {
			fail(c, http.StatusBadRequest, "INVALID_ARGUMENTS", "仅支持查询指定客户的开放工单")
			return
		}
		var customer store.Customer
		result := db.WithContext(c.Request.Context()).Where("code = ?", code).First(&customer)
		if errors.Is(result.Error, gorm.ErrRecordNotFound) {
			fail(c, http.StatusNotFound, "NOT_FOUND", "客户不存在")
			return
		}
		if result.Error != nil {
			fail(c, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "数据服务暂不可用")
			return
		}
		var tickets []store.Ticket
		if err := db.WithContext(c.Request.Context()).Where("customer_id = ? AND status = ?", customer.ID, "open").Order("code").Find(&tickets).Error; err != nil {
			fail(c, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "数据服务暂不可用")
			return
		}
		items := make([]gin.H, 0, len(tickets))
		for _, ticket := range tickets {
			items = append(items, gin.H{"ticket_id": ticket.Code, "status": ticket.Status, "summary": ticket.Title})
		}
		c.JSON(http.StatusOK, gin.H{"customer_code": customer.Code, "tickets": items})
	})
	internal.GET("/mes/work-orders/:work_order_code", func(c *gin.Context) {
		code := c.Param("work_order_code")
		if !workOrderCodePattern.MatchString(code) {
			fail(c, http.StatusBadRequest, "INVALID_ARGUMENTS", "工单标识格式无效")
			return
		}
		var order store.WorkOrder
		result := db.WithContext(c.Request.Context()).Where("code = ?", code).First(&order)
		if errors.Is(result.Error, gorm.ErrRecordNotFound) {
			fail(c, http.StatusNotFound, "NOT_FOUND", "生产工单不存在")
			return
		}
		if result.Error != nil {
			fail(c, http.StatusServiceUnavailable, "DEPENDENCY_UNAVAILABLE", "数据服务暂不可用")
			return
		}
		c.JSON(http.StatusOK, gin.H{
			"work_order_code": order.Code,
			"status":          order.Status,
			"updated_at":      order.UpdatedAt.UTC().Format(time.RFC3339),
		})
	})
	return router
}

func fail(c *gin.Context, status int, code string, message string) {
	c.JSON(status, gin.H{"error": gin.H{"code": code, "message": message}})
}
