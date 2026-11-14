package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/AndreyGurinV/grassCutter/server/internal/api"
	"github.com/AndreyGurinV/grassCutter/server/internal/cli"
)

func main() {
	addr := flag.String("addr", envOr("GRASSCUTTER_ADDR", ":8080"), "HTTP listen address")
	bin := flag.String("bin", envOr("GRASSCUTTER_BIN", "grasscutter"), "grasscutter CLI binary or interpreter")
	binArgs := flag.String("bin-args", envOr("GRASSCUTTER_BIN_ARGS", ""), "extra args before subcommand (e.g. '-m grasscutter.cli')")
	mock := flag.Bool("mock", envBool("GRASSCUTTER_MOCK"), "force mock I2C/GPIO")
	timeout := flag.Duration("timeout", 60*time.Second, "default CLI command timeout")
	flag.Parse()

	runner := cli.NewRunner(cli.Options{
		Bin:     *bin,
		Args:    splitArgs(*binArgs),
		Mock:    *mock,
		Timeout: *timeout,
	})
	defer runner.Close()
	srv := api.New(runner)

	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           logging(srv.Handler()),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		log.Printf("grasscutter HTTP server listening on %s (bin=%q mock=%v)", *addr, *bin, *mock)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	log.Printf("shutting down…")
	_ = runner.Close()
	if err := httpServer.Shutdown(ctx); err != nil {
		log.Fatalf("shutdown: %v", err)
	}
}

func logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rw := &statusWriter{ResponseWriter: w, code: http.StatusOK}
		next.ServeHTTP(rw, r)
		log.Printf("%s %s → %d (%s)", r.Method, r.URL.Path, rw.code, time.Since(start).Round(time.Millisecond))
	})
}

type statusWriter struct {
	http.ResponseWriter
	code int
}

func (w *statusWriter) WriteHeader(code int) {
	w.code = code
	w.ResponseWriter.WriteHeader(code)
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envBool(key string) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	return v == "1" || v == "true" || v == "yes"
}

func splitArgs(s string) []string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	return strings.Fields(s)
}
