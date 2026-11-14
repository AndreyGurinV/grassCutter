package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/AndreyGurinV/grassCutter/server/internal/cli"
)

// Server exposes HTTP endpoints that drive the grasscutter CLI session.
type Server struct {
	runner *cli.Runner
}

// New creates an API server backed by the given CLI runner.
func New(runner *cli.Runner) *Server {
	return &Server{runner: runner}
}

// Handler returns the root HTTP mux.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("POST /arm", s.handleArm)
	mux.HandleFunc("POST /disarm", s.handleDisarm)
	mux.HandleFunc("POST /stop", s.handleStop)
	mux.HandleFunc("POST /drive", s.handleDrive)
	mux.HandleFunc("POST /wheel", s.handleWheel)
	mux.HandleFunc("POST /calibrate-sweep", s.handleCalibrateSweep)
	return mux
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Status(r.Context())
	})
}

func (s *Server) handleArm(w http.ResponseWriter, r *http.Request) {
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Arm(r.Context())
	})
}

func (s *Server) handleDisarm(w http.ResponseWriter, r *http.Request) {
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Disarm(r.Context())
	})
}

func (s *Server) handleStop(w http.ResponseWriter, r *http.Request) {
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Stop(r.Context())
	})
}

func (s *Server) handleDrive(w http.ResponseWriter, r *http.Request) {
	var p cli.DriveParams
	if err := decodeJSON(r, &p); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if p.V < -1 || p.V > 1 || p.W < -1 || p.W > 1 {
		writeError(w, http.StatusBadRequest, "v and w must be in [-1, 1]")
		return
	}
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Drive(r.Context(), p)
	})
}

func (s *Server) handleWheel(w http.ResponseWriter, r *http.Request) {
	var p cli.WheelParams
	if err := decodeJSON(r, &p); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if p.Index < 0 || p.Index > 3 {
		writeError(w, http.StatusBadRequest, "index must be 0..3 (FL FR RL RR)")
		return
	}
	if p.Speed < -1 || p.Speed > 1 {
		writeError(w, http.StatusBadRequest, "speed must be in [-1, 1]")
		return
	}
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.Wheel(r.Context(), p)
	})
}

func (s *Server) handleCalibrateSweep(w http.ResponseWriter, r *http.Request) {
	var p cli.CalibrateParams
	if err := decodeJSON(r, &p); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if p.Channel < 0 || p.Channel > 3 {
		writeError(w, http.StatusBadRequest, "channel must be 0..3")
		return
	}
	s.respondCLI(w, r, func() (*cli.Result, error) {
		return s.runner.CalibrateSweep(r.Context(), p)
	})
}

func (s *Server) respondCLI(w http.ResponseWriter, r *http.Request, fn func() (*cli.Result, error)) {
	res, err := fn()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	body := map[string]any{
		"ok":      res.OK,
		"command": res.Command,
	}
	if res.Status != nil {
		body["status"] = res.Status
	}
	if res.Stdout != "" {
		body["stdout"] = res.Stdout
	}
	if res.Stderr != "" {
		body["stderr"] = res.Stderr
	}
	if res.Error != "" {
		body["error"] = res.Error
	}

	code := http.StatusOK
	if !res.OK {
		code = http.StatusBadGateway
	}
	writeJSON(w, code, body)
}

func decodeJSON(r *http.Request, dst any) error {
	defer r.Body.Close()
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		if errors.Is(err, io.EOF) {
			return errors.New("JSON body required")
		}
		return err
	}
	return nil
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{
		"ok":    false,
		"error": msg,
	})
}
