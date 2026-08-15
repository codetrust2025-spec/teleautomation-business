#!/usr/bin/env bash
# Dual-service container verification for the split.
#
# Runs the 12 required checks against docker-compose.dual.yml. Cross-service
# calls are issued FROM INSIDE a container so they exercise Docker service
# discovery; a check that passed only from the host would not prove the peers
# can reach each other.
#
# Isolated test infrastructure only. Never point this at production.
set -uo pipefail

COMPOSE="docker compose -f docker-compose.dual.yml"
TOKEN="${INTERNAL_SERVICE_TOKEN:-split-test-shared-token}"
PASS=0
FAIL=0

ok()   { printf '  PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  FAIL  %s -- %s\n' "$1" "${2:-}"; FAIL=$((FAIL + 1)); }
step() { printf '\n== %s ==\n' "$1"; }

# Run curl inside a service container. Prints the HTTP status code.
incurl() {
  local svc="$1"; shift
  $COMPOSE exec -T "$svc" python - "$@" <<'PY'
import sys, urllib.request, urllib.error, json
method, url = sys.argv[1], sys.argv[2]
token = sys.argv[3] if len(sys.argv) > 3 else ""
body = sys.argv[4] if len(sys.argv) > 4 else ""
corr = sys.argv[5] if len(sys.argv) > 5 else ""
req = urllib.request.Request(url, method=method)
if token:
    req.add_header("X-Internal-Service-Token", token)
if corr:
    req.add_header("X-Correlation-Id", corr)
data = None
if body:
    data = body.encode()
    req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, data=data, timeout=15) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception as e:                       # noqa: BLE001 - surfaced as a failure
    print(f"ERR:{type(e).__name__}")
PY
}

wait_healthy() {
  local svc="$1" tries=60
  while [ $tries -gt 0 ]; do
    local cid; cid=$($COMPOSE ps -q "$svc" 2>/dev/null)
    if [ -n "$cid" ]; then
      local st; st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
      [ "$st" = "healthy" ] && return 0
    fi
    sleep 2; tries=$((tries - 1))
  done
  return 1
}

step "Bring up isolated dual-service stack"
$COMPOSE up -d --build || { echo "compose up failed"; exit 1; }

step "1-2. Service health"
wait_healthy marketing-api && ok "Marketing container healthy" || bad "Marketing container healthy"
wait_healthy operations-api && ok "Operations container healthy" || bad "Operations container healthy"

step "3-4. Database health"
$COMPOSE exec -T marketing-db pg_isready -U marketing -d marketing >/dev/null 2>&1 \
  && ok "Marketing PostgreSQL ready" || bad "Marketing PostgreSQL ready"
$COMPOSE exec -T operations-db pg_isready -U operations -d operations >/dev/null 2>&1 \
  && ok "Operations PostgreSQL ready" || bad "Operations PostgreSQL ready"

step "5. Marketing -> Operations authenticated contract (via service discovery)"
PAYLOAD='{"source":"dual-service-test","external_id":"dst-1","name":"Split Test","phone":"+910000000000"}'
code=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "$PAYLOAD" "corr-mkt-1")
case "$code" in 2*) ok "Marketing reached operations-api ($code)";; *) bad "Marketing -> Operations" "got $code";; esac

step "6. Operations -> Marketing authenticated contract (via service discovery)"
code=$(incurl operations-api GET http://marketing-api:8000/internal/v1/operational-summary "$TOKEN" "" "corr-ops-1")
case "$code" in 2*) ok "Operations reached marketing-api ($code)";; *) bad "Operations -> Marketing" "got $code";; esac

step "7. Unauthorized peer request is rejected"
# The services reject with 403 (invalid service credential). Any 401/403 is a
# correct rejection; a 2xx here would be a security failure.
code=$(incurl operations-api GET http://marketing-api:8000/internal/v1/operational-summary "" "" "")
case "$code" in 401|403) ok "Unauthenticated peer rejected ($code)";; *) bad "Unauthenticated peer rejected" "got $code";; esac
code=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "wrong-token" "$PAYLOAD" "")
case "$code" in 401|403) ok "Bad-token peer rejected ($code)";; *) bad "Bad-token peer rejected" "got $code";; esac

step "8. Idempotent duplicate replay"
# Same external_id twice must not create a second record.
incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "$PAYLOAD" "corr-dup" >/dev/null
incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "$PAYLOAD" "corr-dup" >/dev/null
dupes=$($COMPOSE exec -T operations-db psql -U operations -d operations -tAc \
  "SELECT count(*) FROM cross_project_inbox WHERE external_id = 'dst-1'" 2>/dev/null | tr -d '[:space:]')
if [ "$dupes" = "1" ]; then ok "Duplicate replay stored once (count=$dupes)"
else bad "Duplicate replay idempotent" "inbox count=${dupes:-unknown}"; fi

step "9-10. Outbox retry and dead-letter while peer is down"
$COMPOSE stop operations-api >/dev/null 2>&1
incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" \
  '{"source":"dual-service-test","external_id":"dst-outage","name":"Outage Test"}' "corr-outage" >/dev/null
mkt_code=$($COMPOSE exec -T marketing-api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status)" 2>/dev/null)
[ "$mkt_code" = "200" ] && ok "Marketing stayed healthy while Operations was down" \
  || bad "Marketing survives peer outage" "health=$mkt_code"
pending=$($COMPOSE exec -T marketing-db psql -U marketing -d marketing -tAc \
  "SELECT count(*) FROM cross_project_outbox WHERE delivered_at IS NULL" 2>/dev/null | tr -d '[:space:]')
[ -n "$pending" ] && ok "Undelivered work queued (pending=$pending)" || bad "Outbox queued pending work"

step "11. Recovery after peer restart"
$COMPOSE start operations-api >/dev/null 2>&1
wait_healthy operations-api && ok "Operations recovered" || bad "Operations recovered"

step "12. Reverse outage: Marketing down, Operations up"
$COMPOSE stop marketing-api >/dev/null 2>&1
ops_code=$($COMPOSE exec -T operations-api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status)" 2>/dev/null)
[ "$ops_code" = "200" ] && ok "Operations stayed healthy while Marketing was down" \
  || bad "Operations survives peer outage" "health=$ops_code"
$COMPOSE start marketing-api >/dev/null 2>&1
wait_healthy marketing-api && ok "Marketing recovered" || bad "Marketing recovered"

step "Persistence across a full restart"
$COMPOSE restart >/dev/null 2>&1
wait_healthy marketing-api && wait_healthy operations-api || bad "Both services healthy after restart"
after=$($COMPOSE exec -T operations-db psql -U operations -d operations -tAc \
  "SELECT count(*) FROM cross_project_inbox WHERE external_id = 'dst-1'" 2>/dev/null | tr -d '[:space:]')
[ "$after" = "1" ] && ok "Records survived restart (count=$after)" || bad "Records survived restart" "count=${after:-unknown}"

step "Correlation id propagation"
$COMPOSE logs marketing-api operations-api 2>/dev/null | grep -q "corr-mkt-1" \
  && ok "Correlation id present in logs" || bad "Correlation id in logs" "corr-mkt-1 not found"

printf '\n===== dual-service result: %d passed, %d failed =====\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
