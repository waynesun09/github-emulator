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
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${CI_STACK_DIR:-$ROOT/.ci-stack}"
PORT="${CI_STACK_PORT:-8000}"
PY="${CI_STACK_PYTHON:-python3}"
LABELS="${CI_STACK_RUNNER_LABELS:-self-hosted,linux,ubuntu-latest,ubuntu-24.04}"
BASE="http://127.0.0.1:$PORT"
ADMIN_TOKEN="${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-ghp_admin_default_token}"

start() {
  mkdir -p "$DIR/data" "$DIR/runner"
  GITHUB_EMULATOR_DATA_DIR="$DIR/data" \
  GITHUB_EMULATOR_DATABASE_URL="sqlite+aiosqlite:///$DIR/data/github_emulator.db" \
  GITHUB_EMULATOR_BASE_URL="$BASE" \
  GITHUB_EMULATOR_HOSTNAME="127.0.0.1" \
  GITHUB_EMULATOR_SSH_ENABLED=false \
  PYTHONPATH="$ROOT/src" \
    nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
      >"$DIR/emulator.log" 2>&1 &
  echo $! >"$DIR/emulator.pid"

  for _ in $(seq 1 60); do
    if curl -sf "$BASE/api/v3" >/dev/null; then break; fi
    sleep 0.5
  done
  curl -sf "$BASE/api/v3" >/dev/null || { tail -50 "$DIR/emulator.log"; exit 1; }

  GITHUB_EMULATOR_URL="$BASE" \
  GITHUB_EMULATOR_TOKEN="$ADMIN_TOKEN" \
  RUNNER_SCOPE=site \
  RUNNER_NAME="ci-runner-1" \
  RUNNER_LABELS="$LABELS" \
  RUNNER_WORKDIR="$DIR/runner/work" \
    nohup "$PY" "$ROOT/src/runners/emulator/runner.py" \
      >"$DIR/runner.log" 2>&1 &
  echo $! >"$DIR/runner.pid"
  echo "emulator $BASE (pid $(cat "$DIR/emulator.pid")), runner pid $(cat "$DIR/runner.pid")"
}

stop() {
  for name in runner emulator; do
    if [ -f "$DIR/$name.pid" ]; then
      kill "$(cat "$DIR/$name.pid")" 2>/dev/null || true
      rm -f "$DIR/$name.pid"
    fi
  done
}

# Print each process's peak resident memory (VmHWM) and CPU seconds so far,
# read from /proc, so Linux only. Job steps run as the runner's children and
# are not counted: their cost belongs to the workflow, not the stack.
stats() {
  local ticks
  ticks="$(getconf CLK_TCK)"
  for name in emulator runner; do
    local pid
    pid="$(cat "$DIR/$name.pid")"
    awk -v n="$name" '/^VmHWM/ {printf "%s peak_rss_mib=%.0f ", n, $2/1024}' "/proc/$pid/status"
    awk -v t="$ticks" '{printf "cpu_s=%.1f\n", ($14+$15)/t}' "/proc/$pid/stat"
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  stats) stats ;;
  *) echo "usage: $0 start|stop|stats" >&2; exit 2 ;;
esac
