#!/usr/bin/env bash
# Deploy grassCutter Go server + web client to Raspberry Pi.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-andrey@192.168.1.231}"
REMOTE_DIR="${REMOTE_DIR:-/home/andrey/grassCutter}"
GOOS="${GOOS:-linux}"
GOARCH="${GOARCH:-arm}"
GOARM="${GOARM:-6}"
# Default 8090: :8080 is already taken on this Pi by another service.
ADDR="${ADDR:-:8090}"
INSTALL_SERVICE="${INSTALL_SERVICE:-1}"
RESTART_SERVICE="${RESTART_SERVICE:-1}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_PYTHON="${SKIP_PYTHON:-0}"

BIN_NAME="grasscutter-server"
STAGE="${ROOT}/deploy/.stage"
REMOTE_TMP="/tmp/grasscutter-deploy-$$"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "need $1 in PATH"; }

need ssh
need rsync
need go
need npm

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)
ssh_cmd=(ssh "${ssh_opts[@]}" "$HOST")
rsync_ssh="ssh ${ssh_opts[*]}"

log "target $HOST:$REMOTE_DIR (GOARCH=$GOARCH GOARM=$GOARM addr=$ADDR)"

"${ssh_cmd[@]}" "echo ok >/dev/null" || die "SSH to $HOST failed (key/agent?)"

rm -rf "$STAGE"
mkdir -p "$STAGE/server" "$STAGE/client" "$STAGE/deploy"

if [[ "$SKIP_BUILD" != "1" ]]; then
  log "build web client"
  (cd "$ROOT/client" && npm ci && npm run build)

  log "cross-compile Go server ($GOOS/$GOARCH GOARM=$GOARM)"
  (
    cd "$ROOT/server"
    CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" GOARM="$GOARM" \
      go build -trimpath -ldflags='-s -w' -o "$STAGE/server/$BIN_NAME" .
  )
else
  log "SKIP_BUILD=1 — expect existing artifacts"
  [[ -x "$ROOT/server/$BIN_NAME" || -x "$STAGE/server/$BIN_NAME" ]] || die "missing server binary"
  [[ -d "$ROOT/client/dist" ]] || die "missing client/dist"
  if [[ ! -x "$STAGE/server/$BIN_NAME" ]]; then
    cp "$ROOT/server/$BIN_NAME" "$STAGE/server/$BIN_NAME"
  fi
fi

log "stage payload"
cp -a "$ROOT/client/dist" "$STAGE/client/dist"
cp "$ROOT/deploy/grasscutter-server.service" "$STAGE/deploy/"
if [[ "$SKIP_BUILD" == "1" && ! -x "$STAGE/server/$BIN_NAME" ]]; then
  die "staged binary missing"
fi
# Prefer freshly built binary in STAGE; also keep a copy path for rsync clarity.
[[ -x "$STAGE/server/$BIN_NAME" ]] || die "binary not staged"

# Python package (CLI session) — required by the Go server.
mkdir -p "$STAGE/grasscutter"
rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$ROOT/grasscutter/" "$STAGE/grasscutter/"
cp "$ROOT/pyproject.toml" "$STAGE/"
cp "$ROOT/requirements.txt" "$STAGE/"
mkdir -p "$STAGE/config"
rsync -a "$ROOT/config/" "$STAGE/config/"

log "upload to $HOST"
"${ssh_cmd[@]}" "mkdir -p '$REMOTE_TMP' '$REMOTE_DIR/server' '$REMOTE_DIR/client' '$REMOTE_DIR/config'"
rsync -az --delete -e "$rsync_ssh" \
  "$STAGE/" "$HOST:$REMOTE_TMP/"

log "install on device"
"${ssh_cmd[@]}" bash -s <<EOF
set -euo pipefail
REMOTE_DIR='$REMOTE_DIR'
REMOTE_TMP='$REMOTE_TMP'
BIN_NAME='$BIN_NAME'
SKIP_PYTHON='$SKIP_PYTHON'
INSTALL_SERVICE='$INSTALL_SERVICE'
RESTART_SERVICE='$RESTART_SERVICE'
ADDR='$ADDR'

mkdir -p "\$REMOTE_DIR/server" "\$REMOTE_DIR/client" "\$REMOTE_DIR/deploy" "\$REMOTE_DIR/config"

install -m 755 "\$REMOTE_TMP/server/\$BIN_NAME" "\$REMOTE_DIR/server/\$BIN_NAME"
rsync -a --delete "\$REMOTE_TMP/client/dist/" "\$REMOTE_DIR/client/dist/"
rsync -a --delete "\$REMOTE_TMP/grasscutter/" "\$REMOTE_DIR/grasscutter/"
cp "\$REMOTE_TMP/pyproject.toml" "\$REMOTE_DIR/pyproject.toml"
cp "\$REMOTE_TMP/requirements.txt" "\$REMOTE_DIR/requirements.txt"
cp "\$REMOTE_TMP/deploy/grasscutter-server.service" "\$REMOTE_DIR/deploy/grasscutter-server.service"
# keep local config.yaml if present; sync examples/defaults only
rsync -a --exclude 'config.yaml' "\$REMOTE_TMP/config/" "\$REMOTE_DIR/config/"

if [[ "\$SKIP_PYTHON" != "1" ]]; then
  if [[ ! -x "\$REMOTE_DIR/.venv/bin/python" ]]; then
    echo "error: missing \$REMOTE_DIR/.venv — create it once on the Pi:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e ." >&2
    exit 1
  fi
  # Refresh editable install offline (sources already synced above).
  "\$REMOTE_DIR/.venv/bin/pip" install -q --no-build-isolation --no-deps -e "\$REMOTE_DIR"
fi

# Bake listen addr into the unit shipped to the Pi.
sed -i "s|-addr :[0-9]*|-addr \$ADDR|" "\$REMOTE_DIR/deploy/grasscutter-server.service"

if [[ "\$INSTALL_SERVICE" == "1" ]]; then
  UNIT_SRC="\$REMOTE_DIR/deploy/grasscutter-server.service"
  UNIT_DST="/etc/systemd/system/grasscutter-server.service"
  if sudo -n true 2>/dev/null; then
    sudo systemctl stop grasscutter-server.service 2>/dev/null || true
    sudo cp "\$UNIT_SRC" "\$UNIT_DST"
    sudo systemctl daemon-reload
    sudo systemctl enable grasscutter-server.service
    if [[ "\$RESTART_SERVICE" == "1" ]]; then
      sudo systemctl restart grasscutter-server.service
      sleep 1
      systemctl --no-pager --full status grasscutter-server.service || true
    fi
  else
    echo "warn: passwordless sudo unavailable — unit copied to \$UNIT_SRC only" >&2
    echo "      install manually: sudo cp \$UNIT_SRC \$UNIT_DST && sudo systemctl enable --now grasscutter-server" >&2
    pkill -f "\$REMOTE_DIR/server/\$BIN_NAME" 2>/dev/null || true
    nohup "\$REMOTE_DIR/server/\$BIN_NAME" \\
      -addr "\$ADDR" \\
      -bin "\$REMOTE_DIR/.venv/bin/grasscutter" \\
      -static "\$REMOTE_DIR/client/dist" \\
      >"\$REMOTE_DIR/server/server.log" 2>&1 &
    echo "started \$BIN_NAME via nohup (pid \$!)"
  fi
fi

rm -rf "\$REMOTE_TMP"
echo "deployed to \$REMOTE_DIR"
EOF

rm -rf "$STAGE"
UI_HOST="${HOST#*@}"
UI_PORT="${ADDR#:}"
log "done — UI http://${UI_HOST}:${UI_PORT}/"
