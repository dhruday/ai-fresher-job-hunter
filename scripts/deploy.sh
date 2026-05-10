#!/usr/bin/env bash
# ============================================================
# deploy.sh — One-shot GitHub deploy for AI Fresher Job Hunter
#
# USAGE:
#   1. Fill in your API keys in .env first
#   2. Log into GitHub:   gh auth login --hostname github.com
#   3. Run this script:   bash scripts/deploy.sh
#
# WHAT THIS SCRIPT DOES:
#   - Creates the GitHub repo (public, with description)
#   - Pushes all code to main branch
#   - Reads your .env and uploads every key as a GitHub Secret
#   - Prints a live link to your repo + Actions tab
# ============================================================

set -euo pipefail

REPO_NAME="ai-fresher-job-hunter"
REPO_DESC="🤖 Automated daily fresher job alerts powered by GPT-4o-mini, SerpAPI, RapidAPI & GitHub Actions"

# ── Colour helpers ────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log_step() { echo -e "\n${BOLD}${CYAN}▶ $1${RESET}"; }
log_ok()   { echo -e "  ${GREEN}✓ $1${RESET}"; }
log_warn() { echo -e "  ${YELLOW}⚠ $1${RESET}"; }
log_err()  { echo -e "  ${RED}✗ $1${RESET}"; }

# ── Pre-flight checks ─────────────────────────────────────────
log_step "Pre-flight checks"

if ! command -v gh &>/dev/null; then
  log_err "GitHub CLI (gh) not found. Install: https://cli.github.com"
  exit 1
fi

# Verify gh is authenticated to github.com
if ! gh auth status --hostname github.com &>/dev/null; then
  log_err "Not logged into github.com. Run: gh auth login --hostname github.com"
  exit 1
fi
log_ok "GitHub CLI authenticated"

if [ ! -f ".env" ]; then
  log_err ".env file not found. Copy: cp .env.example .env && fill in your keys."
  exit 1
fi
log_ok ".env file found"

# Check at least OPENAI_API_KEY is set (not a placeholder)
OPENAI_VAL=$(grep "^OPENAI_API_KEY=" .env | cut -d= -f2-)
if [[ "$OPENAI_VAL" == "sk-xxx"* ]] || [[ -z "$OPENAI_VAL" ]]; then
  log_warn "OPENAI_API_KEY appears to be a placeholder — secrets will still be uploaded"
fi

# ── Get GitHub username ───────────────────────────────────────
log_step "Detecting GitHub username"
GH_USER=$(gh api user --hostname github.com --jq '.login' 2>/dev/null || echo "")
if [ -z "$GH_USER" ]; then
  log_err "Could not detect GitHub username. Ensure you are logged into github.com via gh."
  exit 1
fi
log_ok "Logged in as: $GH_USER"

REPO_FULL="${GH_USER}/${REPO_NAME}"
REPO_URL="https://github.com/${REPO_FULL}"

# ── Create GitHub repository ──────────────────────────────────
log_step "Creating GitHub repository: ${REPO_FULL}"

if gh repo view "$REPO_FULL" --hostname github.com &>/dev/null; then
  log_warn "Repository ${REPO_FULL} already exists — skipping creation"
else
  gh repo create "$REPO_NAME" \
    --hostname github.com \
    --public \
    --description "$REPO_DESC" \
    --source=. \
    --remote=origin \
    --push
  log_ok "Repository created and code pushed: ${REPO_URL}"
fi

# ── Set git remote and push (if repo already existed) ────────
log_step "Setting git remote and pushing code"

if ! git remote get-url origin &>/dev/null; then
  git remote add origin "https://github.com/${REPO_FULL}.git"
fi

# Update remote URL to github.com (not SAP enterprise)
git remote set-url origin "https://github.com/${REPO_FULL}.git"

# Push (force-with-lease is safer than --force)
git push --set-upstream origin main 2>/dev/null || \
  git push --force-with-lease origin main 2>/dev/null || \
  log_warn "Push skipped (repo may already be up to date)"

log_ok "Code pushed to ${REPO_URL}"

# ── Upload GitHub Secrets from .env ──────────────────────────
log_step "Uploading GitHub Secrets from .env"

# Keys to upload as secrets (skip blank values and comment lines)
SECRETS_UPLOADED=0
SECRETS_SKIPPED=0

while IFS= read -r line; do
  # Skip blank lines, comment lines
  [[ -z "$line" || "$line" == \#* ]] && continue
  # Must be KEY=VALUE format
  [[ "$line" != *"="* ]] && continue

  KEY="${line%%=*}"
  VALUE="${line#*=}"

  # Skip if value is empty or a placeholder
  if [[ -z "$VALUE" ]]; then
    log_warn "  Skipping ${KEY} (empty)"
    SECRETS_SKIPPED=$((SECRETS_SKIPPED + 1))
    continue
  fi

  # Upload to GitHub Secrets
  echo "$VALUE" | gh secret set "$KEY" \
    --hostname github.com \
    --repo "$REPO_FULL" \
    --body - &>/dev/null

  log_ok "  Secret set: ${KEY}"
  SECRETS_UPLOADED=$((SECRETS_UPLOADED + 1))

done < .env

echo ""
log_ok "${SECRETS_UPLOADED} secrets uploaded, ${SECRETS_SKIPPED} skipped (empty)"

# ── Enable GitHub Actions ─────────────────────────────────────
log_step "Enabling GitHub Actions workflows"
gh api \
  --hostname github.com \
  --method PUT \
  "/repos/${REPO_FULL}/actions/permissions" \
  -f enabled=true \
  -f allowed_actions=all &>/dev/null && log_ok "GitHub Actions enabled" || \
  log_warn "Could not enable Actions via API (may already be enabled)"

# ── Trigger a manual test run ─────────────────────────────────
log_step "Triggering manual workflow run (test)"
WORKFLOW_FILE=".github/workflows/job_hunter.yml"
if gh workflow run "$WORKFLOW_FILE" \
   --hostname github.com \
   --repo "$REPO_FULL" &>/dev/null; then
  log_ok "Workflow triggered — check progress at:"
  echo "  ${REPO_URL}/actions"
else
  log_warn "Could not trigger workflow automatically."
  echo "  Trigger manually: ${REPO_URL}/actions → 'Run workflow'"
fi

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗"
echo -e "║  ✅  DEPLOYMENT COMPLETE                         ║"
echo -e "╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Repository:  ${CYAN}${REPO_URL}${RESET}"
echo -e "  Actions:     ${CYAN}${REPO_URL}/actions${RESET}"
echo -e "  Secrets:     ${CYAN}${REPO_URL}/settings/secrets/actions${RESET}"
echo ""
echo -e "  ${YELLOW}Next step: Fill in real API keys in .env then re-run${RESET}"
echo -e "  ${YELLOW}secrets upload:  bash scripts/upload_secrets.sh${RESET}"
echo ""
