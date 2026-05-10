#!/usr/bin/env bash
# ============================================================
# upload_secrets.sh — Re-upload GitHub Secrets from .env
#
# Run this any time you update API keys in .env.
# USAGE:  bash scripts/upload_secrets.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log_ok()   { echo -e "  ${GREEN}✓ $1${RESET}"; }
log_warn() { echo -e "  ${YELLOW}⚠ $1${RESET}"; }
log_err()  { echo -e "  ${RED}✗ $1${RESET}"; }

if [ ! -f ".env" ]; then
  log_err ".env not found. Run from project root."
  exit 1
fi

if ! gh auth status --hostname github.com &>/dev/null; then
  log_err "Not logged into github.com. Run: gh auth login --hostname github.com"
  exit 1
fi

GH_USER=$(gh api user --hostname github.com --jq '.login')
REPO_NAME="ai-fresher-job-hunter"
REPO_FULL="${GH_USER}/${REPO_NAME}"

echo -e "\n${BOLD}Uploading secrets from .env → ${REPO_FULL}${RESET}\n"

UPLOADED=0
SKIPPED=0

while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" != *"="* ]] && continue

  KEY="${line%%=*}"
  VALUE="${line#*=}"

  if [[ -z "$VALUE" ]]; then
    log_warn "Skipping ${KEY} (empty)"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  echo "$VALUE" | gh secret set "$KEY" \
    --hostname github.com \
    --repo "$REPO_FULL" \
    --body - &>/dev/null

  log_ok "Set: ${KEY}"
  UPLOADED=$((UPLOADED + 1))
done < .env

echo ""
echo -e "${GREEN}Done: ${UPLOADED} secrets uploaded, ${SKIPPED} skipped.${RESET}"
echo -e "View at: ${CYAN}https://github.com/${REPO_FULL}/settings/secrets/actions${RESET}"
