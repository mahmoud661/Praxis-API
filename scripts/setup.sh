#!/usr/bin/env bash
# Backend developer setup. Run once per fresh clone.
#
#   make setup                         (preferred)
#   bash scripts/setup.sh              (no make required)
#
# Idempotent — safe to re-run after pulling. Skips steps already done.
#
# What this script does:
#   1. Installs the root tooling (lefthook) and wires git hooks.
#   2. Installs node_modules in gateway/ and services/auth/.
#   3. Sets up the Python venv for services/ai-agents (only if `uv` is
#      installed; otherwise warns — work via Docker is unaffected).
#   4. Creates .env from .env.example with a fresh SESSION_SECRET, if
#      .env doesn't already exist.
#
# Does NOT start docker — that's `make up`, on purpose. This script is
# safe to run with Docker stopped.

set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

# ---- output helpers ---------------------------------------------------
# Use ANSI only when stdout is a TTY (CI / piped runs stay readable).
if [ -t 1 ]; then
  C_INFO="\033[36m"; C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_OFF="\033[0m"
else
  C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
say()  { printf "${C_INFO}▸${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_OK}✓${C_OFF} %s\n" "$*"; }
warn() { printf "${C_WARN}!${C_OFF} %s\n" "$*"; }
err()  { printf "${C_ERR}✗${C_OFF} %s\n" "$*" >&2; }
dim()  { printf "${C_DIM}  %s${C_OFF}\n" "$*"; }

# ---- prereq check -----------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

if ! have node || ! have npm; then
  err "node + npm are required. Install Node 22+ (see .nvmrc if present)."
  exit 1
fi

# ---- 1) root tooling + git hooks -------------------------------------
say "Installing root tooling (lefthook)"
npm install --silent --no-audit --no-fund
ok "root tooling installed (git hooks active)"

# ---- 2) per-service node_modules -------------------------------------
for svc in gateway services/auth; do
  if [ -f "$svc/package.json" ]; then
    say "Installing $svc dependencies"
    (cd "$svc" && npm install --silent --no-audit --no-fund)
    ok "$svc ready"
  fi
done

# ---- 3) python venv for ai-agents ------------------------------------
if [ -d services/ai-agents ] && [ -f services/ai-agents/requirements.txt ]; then
  if have uv; then
    say "Setting up Python venv for services/ai-agents"
    (
      cd services/ai-agents
      uv venv .venv >/dev/null
      uv pip install -r requirements.txt --quiet
    )
    ok "ai-agents Python deps ready"
  else
    warn "'uv' not found — skipping ai-agents Python setup."
    dim "Install: pipx install uv   (or)   pip install uv"
    dim "Without uv, you can still run ai-agents via Docker (make up)."
  fi
fi

# ---- 4) .env ----------------------------------------------------------
if [ -f .env ]; then
  ok ".env already exists (left untouched)"
else
  say "Creating .env from .env.example"
  cp .env.example .env

  if have openssl; then
    SECRET=$(openssl rand -hex 32)
    # GNU sed (Linux/Git Bash) and BSD sed (macOS) differ on -i syntax.
    if sed --version >/dev/null 2>&1; then
      sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" .env
    else
      sed -i "" "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" .env
    fi
    ok ".env created with a fresh SESSION_SECRET"
  else
    warn ".env created but SESSION_SECRET still a placeholder (no openssl)."
    dim "Open .env and replace SESSION_SECRET with 32+ random hex chars."
    dim "On Windows PowerShell: [Convert]::ToHexString((1..32 | ForEach-Object { Get-Random -Maximum 256 }))"
  fi
fi

# ---- done -------------------------------------------------------------
echo
ok "Backend setup complete."
dim "Next: 'make up' to start the stack, or 'make help' for all targets."
