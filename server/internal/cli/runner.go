package cli

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"sync"
	"time"
)

// Runner talks to a long-lived `grasscutter session` process.
// Drive/wheel speeds stay applied until stop, disarm, or a new speed command.
type Runner struct {
	bin     string
	args    []string
	mock    bool
	timeout time.Duration

	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	stdout  *bufio.Reader
	stderr  bytesBuffer
	ready   bool
	waitCh  chan error // closed/finished when process exits
}

// Options configures a Runner.
type Options struct {
	Bin     string
	Args    []string
	Mock    bool
	Timeout time.Duration
}

// NewRunner builds a session runner. Bin defaults to "grasscutter".
func NewRunner(opts Options) *Runner {
	bin := opts.Bin
	if bin == "" {
		bin = "grasscutter"
	}
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	return &Runner{
		bin:     bin,
		args:    append([]string(nil), opts.Args...),
		mock:    opts.Mock,
		timeout: timeout,
	}
}

// Result is the outcome of a session command.
type Result struct {
	OK      bool           `json:"ok"`
	Status  map[string]any `json:"status,omitempty"`
	Stdout  string         `json:"stdout,omitempty"`
	Stderr  string         `json:"stderr,omitempty"`
	Command string         `json:"command"`
	Error   string         `json:"error,omitempty"`
}

// DriveParams are arguments for the drive command.
type DriveParams struct {
	V float64 `json:"v"`
	W float64 `json:"w"`
}

// WheelParams are arguments for the wheel command.
type WheelParams struct {
	Index int     `json:"index"`
	Speed float64 `json:"speed"`
}

// CalibrateParams are arguments for calibrate-sweep.
type CalibrateParams struct {
	Channel int     `json:"channel"`
	FromV   float64 `json:"from_v"`
	ToV     float64 `json:"to_v"`
	Step    float64 `json:"step"`
	Delay   float64 `json:"delay"`
}

// Close stops the session process (fail-safe on CLI exit).
func (r *Runner) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stopSessionLocked()
}

func (r *Runner) Status(ctx context.Context) (*Result, error) {
	return r.call(ctx, map[string]any{"cmd": "status"})
}

func (r *Runner) Arm(ctx context.Context) (*Result, error) {
	return r.call(ctx, map[string]any{"cmd": "arm"})
}

func (r *Runner) Disarm(ctx context.Context) (*Result, error) {
	return r.call(ctx, map[string]any{"cmd": "disarm"})
}

func (r *Runner) Stop(ctx context.Context) (*Result, error) {
	return r.call(ctx, map[string]any{"cmd": "stop"})
}

func (r *Runner) Drive(ctx context.Context, p DriveParams) (*Result, error) {
	return r.call(ctx, map[string]any{"cmd": "drive", "v": p.V, "w": p.W})
}

func (r *Runner) Wheel(ctx context.Context, p WheelParams) (*Result, error) {
	if p.Index < 0 || p.Index > 3 {
		return nil, fmt.Errorf("index must be 0..3 (FL FR RL RR)")
	}
	return r.call(ctx, map[string]any{"cmd": "wheel", "index": p.Index, "speed": p.Speed})
}

func (r *Runner) CalibrateSweep(ctx context.Context, p CalibrateParams) (*Result, error) {
	if p.Channel < 0 || p.Channel > 3 {
		return nil, fmt.Errorf("channel must be 0..3")
	}
	fromV, toV, step, delay := p.FromV, p.ToV, p.Step, p.Delay
	if toV == 0 && fromV == 0 {
		toV = 4.2
	}
	if step == 0 {
		step = 0.2
	}
	if delay == 0 {
		delay = 0.5
	}
	return r.call(ctx, map[string]any{
		"cmd":     "calibrate-sweep",
		"channel": p.Channel,
		"from_v":  fromV,
		"to_v":    toV,
		"step":    step,
		"delay":   delay,
	})
}

func (r *Runner) call(ctx context.Context, req map[string]any) (*Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if err := r.ensureSessionLocked(ctx); err != nil {
		return nil, err
	}

	timeout := r.timeout
	if req["cmd"] == "calibrate-sweep" {
		timeout = 5 * time.Minute
	}
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	payload, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	cmdline := string(payload)

	if _, err := r.stdin.Write(append(payload, '\n')); err != nil {
		_ = r.stopSessionLocked()
		return nil, fmt.Errorf("write session: %w", err)
	}

	type readResult struct {
		line string
		err  error
	}
	ch := make(chan readResult, 1)
	go func() {
		line, err := r.stdout.ReadString('\n')
		ch <- readResult{line: line, err: err}
	}()

	var line string
	select {
	case <-callCtx.Done():
		_ = r.stopSessionLocked()
		return &Result{
			OK:      false,
			Command: cmdline,
			Error:   "command timed out",
			Stderr:  r.stderr.Snapshot(),
		}, nil
	case rr := <-ch:
		if rr.err != nil {
			_ = r.stopSessionLocked()
			return nil, fmt.Errorf("read session: %w", rr.err)
		}
		line = rr.line
	}

	var resp struct {
		OK     bool           `json:"ok"`
		Status map[string]any `json:"status"`
		Stdout string         `json:"stdout"`
		Error  string         `json:"error"`
		Event  string         `json:"event"`
	}
	if err := json.Unmarshal([]byte(line), &resp); err != nil {
		return &Result{
			OK:      false,
			Command: cmdline,
			Error:   fmt.Sprintf("invalid session response: %v", err),
			Stdout:  line,
			Stderr:  r.stderr.Snapshot(),
		}, nil
	}

	return &Result{
		OK:      resp.OK,
		Status:  resp.Status,
		Stdout:  resp.Stdout,
		Error:   resp.Error,
		Command: cmdline,
		Stderr:  r.stderr.Snapshot(),
	}, nil
}

func (r *Runner) ensureSessionLocked(ctx context.Context) error {
	if r.ready && r.cmd != nil && r.cmd.Process != nil {
		return nil
	}
	return r.startSessionLocked(ctx)
}

func (r *Runner) startSessionLocked(ctx context.Context) error {
	_ = r.stopSessionLocked()

	args := append([]string(nil), r.args...)
	if r.mock {
		args = append(args, "--mock")
	}
	args = append(args, "session")

	cmd := exec.Command(r.bin, args...)
	cmd.Env = os.Environ()
	if r.mock {
		cmd.Env = append(cmd.Env, "GRASSCUTTER_MOCK=1")
	}

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return err
	}
	r.stderr.Reset()
	cmd.Stderr = &r.stderr

	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		return fmt.Errorf("start session: %w", err)
	}

	waitCh := make(chan error, 1)
	go func() {
		waitCh <- cmd.Wait()
		close(waitCh)
	}()

	r.cmd = cmd
	r.stdin = stdin
	r.stdout = bufio.NewReader(stdout)
	r.waitCh = waitCh
	r.ready = false

	readyCtx, cancel := context.WithTimeout(ctx, r.timeout)
	defer cancel()

	type readResult struct {
		line string
		err  error
	}
	ch := make(chan readResult, 1)
	go func() {
		line, err := r.stdout.ReadString('\n')
		ch <- readResult{line: line, err: err}
	}()

	select {
	case <-readyCtx.Done():
		_ = r.stopSessionLocked()
		return fmt.Errorf("session ready timed out: %s", r.stderr.Snapshot())
	case err := <-waitCh:
		r.clearSessionLocked()
		if err != nil {
			return fmt.Errorf("session exited during ready: %w (%s)", err, r.stderr.Snapshot())
		}
		return fmt.Errorf("session exited during ready (%s)", r.stderr.Snapshot())
	case rr := <-ch:
		if rr.err != nil {
			_ = r.stopSessionLocked()
			return fmt.Errorf("session ready: %w (%s)", rr.err, r.stderr.Snapshot())
		}
		var ready struct {
			OK    bool   `json:"ok"`
			Event string `json:"event"`
		}
		if err := json.Unmarshal([]byte(rr.line), &ready); err != nil || !ready.OK || ready.Event != "ready" {
			_ = r.stopSessionLocked()
			return fmt.Errorf("unexpected ready line: %s", rr.line)
		}
	}

	r.ready = true
	log.Printf("grasscutter session started (pid=%d)", cmd.Process.Pid)
	return nil
}

func (r *Runner) clearSessionLocked() {
	r.ready = false
	r.cmd = nil
	r.stdin = nil
	r.stdout = nil
	r.waitCh = nil
}

func (r *Runner) stopSessionLocked() error {
	if r.cmd == nil {
		r.clearSessionLocked()
		return nil
	}

	cmd := r.cmd
	waitCh := r.waitCh
	if r.stdin != nil {
		_, _ = r.stdin.Write([]byte(`{"cmd":"quit"}` + "\n"))
		_ = r.stdin.Close()
	}
	r.clearSessionLocked()

	select {
	case <-waitCh:
		return nil
	case <-time.After(2 * time.Second):
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
		<-waitCh
		return nil
	}
}

// bytesBuffer is a concurrency-safe stderr sink.
type bytesBuffer struct {
	mu sync.Mutex
	b  []byte
}

func (b *bytesBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.b = append(b.b, p...)
	if len(b.b) > 64*1024 {
		b.b = b.b[len(b.b)-32*1024:]
	}
	return len(p), nil
}

func (b *bytesBuffer) Reset() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.b = nil
}

func (b *bytesBuffer) Snapshot() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return string(b.b)
}
