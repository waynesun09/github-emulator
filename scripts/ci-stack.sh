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
#   CI_STACK_TLS    1 to serve https from uvicorn itself, with a CA and a
#                   certificate for CI_STACK_HOST made per run under
#                   $CI_STACK_DIR/tls. Clients must trust tls/ca.pem; the
#                   Python runner is pointed at it. Needed when a client
#                   insists on https (gh, the OpenShell egress proxy).
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
TLS="${CI_STACK_TLS:-0}"
SCHEME=http
[ "$TLS" = 1 ] && SCHEME=https
BASE="$SCHEME://$HOST:$PORT"
# Readiness is checked on loopback: CI_STACK_HOST may name an address this
# machine cannot reach itself (one inside a VM), and only clients use it.
LOCAL="$SCHEME://127.0.0.1:$PORT"
if [ "$BIND" != 0.0.0.0 ]; then LOCAL="$SCHEME://$BIND:$PORT"; fi
ADMIN_TOKEN="${GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN:-ghp_admin_default_token}"
CA="$DIR/tls/ca.pem"

# A throwaway CA and a server certificate for HOST and loopback.
make_tls() {
  local t="$DIR/tls" san
  mkdir -p "$t"
  if [[ "$HOST" =~ ^[0-9.]+$ ]]; then san="IP:$HOST"; else san="DNS:$HOST"; fi
  san="$san,DNS:localhost,IP:127.0.0.1"
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=ci-stack CA" \
    -keyout "$t/ca-key.pem" -out "$CA" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$HOST" \
    -keyout "$t/server-key.pem" -out "$t/server.csr" 2>/dev/null
  printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' "$san" >"$t/ext.cnf"
  openssl x509 -req -in "$t/server.csr" -CA "$CA" -CAkey "$t/ca-key.pem" \
    -CAcreateserial -days 2 -extfile "$t/ext.cnf" -out "$t/server.pem" 2>/dev/null
  chmod 600 "$t"/*-key.pem
}

start() {
  mkdir -p "$DIR/data" "$DIR/runner"
  local tls_args=() curl_ca=()
  if [ "$TLS" = 1 ]; then
    make_tls
    tls_args=(--ssl-certfile "$DIR/tls/server.pem" --ssl-keyfile "$DIR/tls/server-key.pem")
    curl_ca=(--cacert "$CA")
  fi
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
      ${tls_args[@]+"${tls_args[@]}"} >"$DIR/emulator.log" 2>&1 &
  echo $! >"$DIR/emulator.pid"

  for _ in $(seq 1 60); do
    if curl -sf -m 2 ${curl_ca[@]+"${curl_ca[@]}"} "$LOCAL/api/v3" >/dev/null; then break; fi
    sleep 0.5
  done
  curl -sf -m 2 ${curl_ca[@]+"${curl_ca[@]}"} "$LOCAL/api/v3" >/dev/null ||
    { tail -50 "$DIR/emulator.log"; exit 1; }

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
  if [ "$TLS" = 1 ]; then
    echo "trust $CA: SSL_CERT_FILE / GIT_SSL_CAINFO, or the system store for actions/runner"
  fi
}

start_python_runner() {
  local i="$1"
  if [ "$TLS" = 1 ]; then
    # httpx in runner.py and git in its checkout step read these.
    export SSL_CERT_FILE="$CA" GIT_SSL_CAINFO="$CA"
  fi
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
  local f pid
  for f in $(pidfiles); do
    pid="$(cat "$f")"
    kill "$pid" 2>/dev/null || true
    # uvicorn can outlive TERM; give it 10 seconds, then KILL.
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
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
