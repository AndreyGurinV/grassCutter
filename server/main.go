package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
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
	staticDir := flag.String("static", envOr("GRASSCUTTER_STATIC", defaultStaticDir()), "web UI static files directory (empty to disable)")
	flag.Parse()

	runner := cli.NewRunner(cli.Options{
		Bin:     *bin,
		Args:    splitArgs(*binArgs),
		Mock:    *mock,
		Timeout: *timeout,
	})
	defer runner.Close()

	static := strings.TrimSpace(*staticDir)
	if static != "" {
		if st, err := os.Stat(static); err != nil || !st.IsDir() {
			log.Printf("static UI dir %q not found — API only", static)
			static = ""
		}
	}
	srv := api.New(runner, api.Options{StaticDir: static})

	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           logging(srv.Handler()),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		log.Printf("grasscutter HTTP server listening on %s (bin=%q mock=%v static=%q)", *addr, *bin, *mock, static)
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

func defaultStaticDir() string {
	candidates := []string{
		"client/dist",
		"../client/dist",
	}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		candidates = append([]string{
			filepath.Join(dir, "client", "dist"),
			filepath.Join(dir, "..", "client", "dist"),
		}, candidates...)
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			abs, err := filepath.Abs(c)
			if err == nil {
				return abs
			}
			return c
		}
	}
	return "../client/dist"
}
