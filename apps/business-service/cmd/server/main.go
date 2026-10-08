package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"opspilot/business-service/internal/store"
	"opspilot/business-service/internal/transport"
)

func main() {
	db, err := store.OpenMySQLFromEnv()
	if err != nil {
		log.Fatal(err)
	}
	if err := store.Migrate(db); err != nil {
		log.Fatal(err)
	}
	token := os.Getenv("INTERNAL_SERVICE_TOKEN")
	if len(token) < 16 {
		log.Fatal("INTERNAL_SERVICE_TOKEN 至少需要 16 字节")
	}
	router := transport.NewRouter(db, token)
	address := os.Getenv("BUSINESS_LISTEN_ADDR")
	if address == "" {
		address = "127.0.0.1:8082"
	}
	server := &http.Server{
		Addr:              address,
		Handler:           router,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
