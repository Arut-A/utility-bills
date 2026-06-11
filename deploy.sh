#!/bin/bash
set -euo pipefail

# Bills pipeline deploy script
# Usage: ./deploy.sh [service]     — deploy specific service (api-server, bill-parser, gmail-scraper)
#        ./deploy.sh               — deploy all services
#        ./deploy.sh --test-only   — run tests without deploying

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DOCKER="/usr/local/bin/docker"
COMPOSE="$DOCKER compose"
BACKUP_DIR="/volume1/Projects/bills/backups"
SERVICE="${1:-all}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# ── Step 1: Pre-flight checks ──────────────────────────────────────────────
log "Pre-flight checks..."
[ -f ".env" ] || fail ".env file missing"
[ -f "docker-compose.yml" ] || fail "docker-compose.yml missing"
[ -f "config/vendors.yaml" ] || fail "vendors.yaml missing"

# ── Step 2: Run tests ───────────────────────────────────────────────────────
log "Running tests..."
if [ -d "tests" ] && [ -f "tests/conftest.py" ]; then
    # Host python is 3.8 without the API deps — run tests hermetically in the
    # same image family as the runtime containers (python:3.12-slim).
    $DOCKER run --rm -v "$SCRIPT_DIR":/w -w /w python:3.12-slim bash -c \
        "pip install -q -r api-server/requirements.txt -r bill-parser/requirements.txt pytest httpx >/dev/null 2>&1 && python -m pytest tests/unit -q --tb=short" \
        || fail "Unit tests failed — aborting deploy"
    log "Tests passed."
else
    warn "No tests found — skipping (set up tests/ directory)"
fi

if [ "$SERVICE" = "--test-only" ]; then
    log "Test-only mode — done."
    exit 0
fi

# ── Step 3: Validate vendors.yaml ───────────────────────────────────────────
log "Validating vendors.yaml..."
$DOCKER run --rm -v "$SCRIPT_DIR":/w -w /w python:3.12-slim bash -c \
    "pip install -q pyyaml >/dev/null 2>&1 && python scripts/validate_vendors.py" \
    || fail "Config validation failed — aborting deploy"

# ── Step 3b: Sync shared modules ────────────────────────────────────────────
log "Syncing shared modules..."
cp bill-parser/vendor_config.py gmail-scraper/vendor_config.py
log "vendor_config.py synced to gmail-scraper."

# ── Step 4: Backup database ────────────────────────────────────────────────
log "Backing up database..."
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/bills_$(date +%Y%m%d_%H%M%S).sql.gz"
if $DOCKER inspect bills_mariadb >/dev/null 2>&1; then
    source .env
    $DOCKER exec bills_mariadb mysqldump -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DB" 2>/dev/null \
        | gzip > "$BACKUP_FILE" \
        && log "DB backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))" \
        || warn "DB backup failed — continuing (first deploy?)"
else
    warn "MariaDB not running — skipping backup (first deploy?)"
fi

# ── Step 5: Build, tag, and deploy ────────────────────────────────────────
TAG=$(date +%Y%m%d)
log "Image tag: $TAG"

if [ "$SERVICE" = "all" ]; then
    log "Building and deploying all services..."
    $COMPOSE build
    # Tag images with date for rollback
    for img in bills-api bills-parser bills-scraper; do
        $DOCKER tag ${img}:latest ${img}:${TAG} 2>/dev/null && log "Tagged ${img}:${TAG}"
    done
    $COMPOSE up -d
else
    log "Building and deploying $SERVICE..."
    $COMPOSE build "$SERVICE"
    $COMPOSE up -d --no-deps "$SERVICE"
fi

# ── Step 6: Wait and health check ──────────────────────────────────────────
log "Waiting for services to start..."
sleep 10

HEALTHY=true

# Check MariaDB
if $DOCKER inspect --format='{{.State.Health.Status}}' bills_mariadb 2>/dev/null | grep -q healthy; then
    log "MariaDB: healthy"
else
    warn "MariaDB: not healthy yet, waiting..."
    sleep 15
    if $DOCKER inspect --format='{{.State.Health.Status}}' bills_mariadb 2>/dev/null | grep -q healthy; then
        log "MariaDB: healthy"
    else
        fail "MariaDB: unhealthy after 25s"
        HEALTHY=false
    fi
fi

# Check API server
if curl -sf http://localhost:8888/health >/dev/null 2>&1; then
    log "API server: healthy"
else
    warn "API server: unhealthy"
    HEALTHY=false
fi

# Check parser (via docker network, not exposed port)
PARSER_STATUS=$($DOCKER exec bills_api curl -sf http://bill-parser:8001/health 2>/dev/null && echo "ok" || echo "fail")
if [ "$PARSER_STATUS" = "ok" ]; then
    log "Bill parser: healthy"
else
    warn "Bill parser: unhealthy"
    HEALTHY=false
fi

# Check scraper is running
if $DOCKER inspect --format='{{.State.Status}}' bills_gmail_scraper 2>/dev/null | grep -q running; then
    log "Gmail scraper: running"
else
    warn "Gmail scraper: not running"
    HEALTHY=false
fi

# ── Step 7: Result ─────────────────────────────────────────────────────────
echo ""
if [ "$HEALTHY" = true ]; then
    log "Deploy successful. All services healthy."
    # Clean old backups (keep last 30)
    find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete 2>/dev/null
else
    warn "Deploy completed with warnings. Check unhealthy services above."
    warn "To rollback: git checkout HEAD~1 -- . && ./deploy.sh"
fi
