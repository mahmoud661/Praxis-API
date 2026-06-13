#!/usr/bin/env bash
# backend/scripts/smoke-test.sh
#
# End-to-end smoke test: boots the compose stack, exercises the core auth +
# threads flow (signup → login → create thread), then tears down.
#
# Requires:
#   - Docker Engine with Compose V2 plugin (`docker compose`)
#   - curl and jq
#
# Run from the repo root:
#   bash backend/scripts/smoke-test.sh
#
# CI passes GATEWAY_PORT / SMOKE_TIMEOUT_S as env vars; defaults suit
# the standard compose config.
#
# What is NOT tested here (requires a live LLM key):
#   chat round-trip — POST /v1/threads/{id}/runs or the WS agent endpoint.
#   Wire those via LITELLM_* secrets in your CD pipeline's smoke gate.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_DIR="${REPO_ROOT}/backend/infra"
GATEWAY_PORT="${GATEWAY_PORT:-4000}"
SMOKE_TIMEOUT_S="${SMOKE_TIMEOUT_S:-120}"

GW="http://localhost:${GATEWAY_PORT}"

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
log()  { echo "[smoke] $*"; }
fail() { echo "[smoke] FAIL: $*" >&2; exit 1; }

require_cmd() {
  command -v "$1" &>/dev/null || fail "'$1' not found — install it and retry"
}

wait_for_url() {
  local url="$1" label="$2" deadline=$(( $(date +%s) + SMOKE_TIMEOUT_S ))
  log "waiting for ${label} (${url}) …"
  until curl -sf "${url}" &>/dev/null; do
    (( $(date +%s) < deadline )) || fail "timeout waiting for ${label}"
    sleep 3
  done
  log "${label} is up"
}

cleanup() {
  log "tearing down compose stack …"
  docker compose \
    -f "${INFRA_DIR}/docker-compose.yml" \
    -f "${INFRA_DIR}/docker-compose.ci.yml" \
    --env-file "${INFRA_DIR}/.env.ci" \
    down -v --remove-orphans 2>/dev/null || true
}

# --------------------------------------------------------------------------
# pre-flight
# --------------------------------------------------------------------------
require_cmd docker
require_cmd curl
require_cmd jq

# Ensure env file exists (CI writes it before calling us; local runs use a
# pre-existing file or the default that setup.sh generates).
ENV_FILE="${INFRA_DIR}/.env.ci"
if [[ ! -f "${ENV_FILE}" ]]; then
  fail ".env.ci not found at ${ENV_FILE} — create it before running this script"
fi

# --------------------------------------------------------------------------
# boot
# --------------------------------------------------------------------------
log "starting compose stack (CI overlay) …"
docker compose \
  -f "${INFRA_DIR}/docker-compose.yml" \
  -f "${INFRA_DIR}/docker-compose.ci.yml" \
  --env-file "${ENV_FILE}" \
  up -d --build --wait 2>&1 | tail -20 || true

# Register cleanup on EXIT so the stack always comes down, even on failure.
trap cleanup EXIT

# --------------------------------------------------------------------------
# wait for the gateway
# --------------------------------------------------------------------------
wait_for_url "${GW}/healthz" "gateway /healthz"

# /readyz checks the Redis connection — only pass this hurdle once the
# gateway's own dependencies are up.
wait_for_url "${GW}/readyz" "gateway /readyz"

# --------------------------------------------------------------------------
# test 1: register a new user
# --------------------------------------------------------------------------
SMOKE_EMAIL="smoke-$(date +%s)@ci.example.com"
SMOKE_PASS="SmokeTest1!"

log "POST /v1/auth/register …"
REG_BODY=$(curl -sf -X POST "${GW}/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${SMOKE_EMAIL}\",\"password\":\"${SMOKE_PASS}\"}" \
  -c /tmp/smoke-cookies.txt) \
  || fail "register request failed"

echo "${REG_BODY}" | jq -e '.userId' &>/dev/null \
  || fail "register response missing userId — got: ${REG_BODY}"
log "register OK ($(echo "${REG_BODY}" | jq -r '.userId'))"

# --------------------------------------------------------------------------
# test 2: log in and get a session cookie
# --------------------------------------------------------------------------
log "POST /v1/auth/login …"
LOGIN_BODY=$(curl -sf -X POST "${GW}/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${SMOKE_EMAIL}\",\"password\":\"${SMOKE_PASS}\"}" \
  -b /tmp/smoke-cookies.txt \
  -c /tmp/smoke-cookies.txt) \
  || fail "login request failed"

echo "${LOGIN_BODY}" | jq -e '.userId' &>/dev/null \
  || fail "login response missing userId — got: ${LOGIN_BODY}"
log "login OK ($(echo "${LOGIN_BODY}" | jq -r '.userId'))"

# --------------------------------------------------------------------------
# test 3: create a chat thread (requires auth session)
# --------------------------------------------------------------------------
log "POST /v1/threads …"
THREAD_BODY=$(curl -sf -X POST "${GW}/v1/threads" \
  -H "Content-Type: application/json" \
  -b /tmp/smoke-cookies.txt \
  -c /tmp/smoke-cookies.txt \
  -d '{}') \
  || fail "create thread request failed"

echo "${THREAD_BODY}" | jq -e '.id' &>/dev/null \
  || fail "create thread response missing id — got: ${THREAD_BODY}"
THREAD_ID=$(echo "${THREAD_BODY}" | jq -r '.id')
log "create thread OK (${THREAD_ID})"

# --------------------------------------------------------------------------
# test 4: verify the thread appears in the listing
# --------------------------------------------------------------------------
log "GET /v1/threads …"
LIST_BODY=$(curl -sf "${GW}/v1/threads" \
  -b /tmp/smoke-cookies.txt) \
  || fail "list threads request failed"

echo "${LIST_BODY}" | jq -e --arg tid "${THREAD_ID}" \
  '[.threads[] | select(.id == $tid)] | length > 0' &>/dev/null \
  || fail "new thread ${THREAD_ID} not found in listing — got: ${LIST_BODY}"
log "list threads OK — thread ${THREAD_ID} is visible"

# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------
log "smoke test PASSED"
rm -f /tmp/smoke-cookies.txt
