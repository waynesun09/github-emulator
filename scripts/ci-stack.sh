#!/usr/bin/env bash
# Start the emulator and its Python runner as two plain processes: SQLite,
# plain HTTP, no Caddy, no container and no cluster. This is the smallest
# stack that executes a workflow run end to end, and it is what the CI
# workflow uses.
#
#   scripts/ci-stack.sh start   # start both, wait until the API answers
#   scripts/ci-stack.sh stop    # stop both
#
# Settings (all optional):
#   CI_STACK_DIR    state directory: SQLite file, git repos, logs, pids
#   CI_STACK_PORT   API port (default 8000)
#   CI_STACK_PYTHON python with requirements.txt installed (default python3)
#   CI_STACK_RUNNER_LABELS  labels the runner serves
#   CI_STACK_HOST   host name the emulator puts in its URLs (default
#                   127.0.0.1). A sandbox that cannot reach loopback needs a
#                   name that resolves to this machine, e.g. <ip>.nip.io.
#   CI_STACK_BIND   address uvicorn listens on (default 127.0.0.1; use
#                   0.0.0.0 with a non-loopback CI_STACK_HOST)
#   CI_STACK_RUNNERS  runner processes, i.e. jobs that run at once (default 1)
#   CI_STACK_RUNNER_KIND  python (src/runners/emulator/runner.py, default) or
#                   upstream (the real actions/runner, Linux x64)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${CI_STACK_DIR:-$ROOT/.ci-stack}"
PORT="${CI_STACK_PORT:-8000}"
PY="${CI_STACK_PYTHON:-python3}"
LABELS="${CI_STACK_RUNNER_LABELS:-self-hosted,linux,ubuntu-latest,ubuntu-24.04}"
HOST="${CI_STACK_HOST:-127.0.0.1}"
BIND="${CI_STACK_BIND:-127.0.0.1}"
RUNNERS="${CI_STACK_RUNNERS:-1}"
KIND="${CI_STACK_RUNNER_KIND:-python}"
UPSTREAM_VERSION="${CI_STACK_UPSTREAM_RUNNER_VERSION:-2.317.0}"
BASE="http://$HOST:$PORT"
ADMIN_TOKEN="${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-ghp_admin_default_token}"

start() {
  mkdir -p "$DIR/data" "$DIR/runner"
  # Job tokens and sessions are signed with SECRET_KEY, so never run on the
  # published default; and App JWTs are verified, not just decoded.
  GITHUB_EMULATOR_SECRET_KEY="${GITHUB_EMULATOR_SECRET_KEY:-$(openssl rand -hex 32)}" \
  GITHUB_EMULATOR_APP_JWT_PERMISSIVE="${GITHUB_EMULATOR_APP_JWT_PERMISSIVE:-false}" \
  GITHUB_EMULATOR_DATA_DIR="$DIR/data" \
  GITHUB_EMULATOR_DATABASE_URL="sqlite+aiosqlite:///$DIR/data/github_emulator.db" \
  GITHUB_EMULATOR_BASE_URL="$BASE" \
  GITHUB_EMULATOR_HOSTNAME="$HOST" \
  GITHUB_EMULATOR_SSH_ENABLED=false \
  PYTHONPATH="$ROOT/src" \
    nohup "$PY" -m uvicorn app.main:app --host "$BIND" --port "$PORT" \
      >"$DIR/emulator.log" 2>&1 &
  echo $! >"$DIR/emulator.pid"

  for _ in $(seq 1 60); do
    if curl -sf "$BASE/api/v3" >/dev/null; then break; fi
    sleep 0.5
  done
  curl -sf "$BASE/api/v3" >/dev/null || { tail -50 "$DIR/emulator.log"; exit 1; }

  # One runner executes one job at a time, so job concurrency is the number
  # of runner processes. Each gets its own work directory.
  for i in $(seq 1 "$RUNNERS"); do
    case "$KIND" in
      python) start_python_runner "$i" ;;
      upstream) start_upstream_runner "$i" ;;
      *) echo "CI_STACK_RUNNER_KIND must be python or upstream, got $KIND" >&2; exit 2 ;;
    esac
  done
  echo "emulator $BASE (pid $(cat "$DIR/emulator.pid")), $RUNNERS $KIND runner(s)"
}

start_python_runner() {
  local i="$1"
  GITHUB_EMULATOR_URL="$BASE" \
  GITHUB_EMULATOR_TOKEN="$ADMIN_TOKEN" \
  RUNNER_SCOPE=site \
  RUNNER_NAME="ci-runner-$i" \
  RUNNER_LABELS="$LABELS" \
  RUNNER_WORKDIR="$DIR/runner-$i/work" \
    nohup "$PY" "$ROOT/src/runners/emulator/runner.py" \
      >"$DIR/runner-$i.log" 2>&1 &
  echo $! >"$DIR/runner-$i.pid"
}

# The real actions/runner, registered at enterprise scope the way
# src/runners/upstream/entrypoint.sh does it. Linux x64 only.
start_upstream_runner() {
  local i="$1" home="$DIR/runner-$i" enterprise="${CI_STACK_ENTERPRISE:-breadboard}"
  local tarball="$DIR/actions-runner-$UPSTREAM_VERSION.tar.gz"
  if [ ! -f "$tarball" ]; then
    curl -fsSL -o "$tarball" \
      "https://github.com/actions/runner/releases/download/v$UPSTREAM_VERSION/actions-runner-linux-x64-$UPSTREAM_VERSION.tar.gz"
  fi
  mkdir -p "$home"
  tar -xzf "$tarball" -C "$home"
  local reg
  reg="$(curl -sf -X POST -H "Authorization: token $ADMIN_TOKEN" \
    "$BASE/api/v3/enterprises/$enterprise/actions/runners/registration-token" | jq -r .token)"
  (cd "$home" && ./config.sh --unattended --replace \
    --url "$BASE/enterprises/$enterprise" --token "$reg" \
    --name "ci-runner-$i" --labels "$LABELS" --work _work) >"$DIR/runner-$i.log" 2>&1
  (cd "$home" && nohup ./run.sh >>"$DIR/runner-$i.log" 2>&1 &
   echo $! >"$DIR/runner-$i.pid")
}

pidfiles() {
  ls "$DIR"/runner-*.pid "$DIR/emulator.pid" 2>/dev/null || true
}

stop() {
  for f in $(pidfiles); do
    kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
  # run.sh leaves Runner.Listener and Runner.Worker behind.
  pkill -f "$DIR/runner-" 2>/dev/null || true
}

# Print each process's peak resident memory (VmHWM) and CPU seconds so far,
# read from /proc, so Linux only. Job steps run as the runner's children and
# are not counted: their cost belongs to the workflow, not the stack.
stats() {
  local ticks
  ticks="$(getconf CLK_TCK)"
  for f in $(pidfiles); do
    local pid name
    pid="$(cat "$f")"
    name="$(basename "$f" .pid)"
    awk -v n="$name" '/^VmHWM/ {printf "%s peak_rss_mib=%.0f ", n, $2/1024}' "/proc/$pid/status"
    awk -v t="$ticks" '{printf "cpu_s=%.1f\n", ($14+$15)/t}' "/proc/$pid/stat"
  done
  # The upstream runner's pid file names run.sh; its .NET listener and worker
  # are children. Report their current resident memory as a group.
  ps -eo rss=,args= | awk -v d="$DIR/runner-" 'index($0, d) {s += $1} END {printf "all runner processes now rss_mib=%.0f\n", s/1024}'
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  stats) stats ;;
  *) echo "usage: $0 start|stop|stats" >&2; exit 2 ;;
esac
