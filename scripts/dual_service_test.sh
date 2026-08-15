#!/usr/bin/env bash
# Dual-service container verification for the split.
#
# Cross-service calls are issued FROM INSIDE a container so they exercise Docker
# service discovery. A check that passed only from the host would not prove the
# peers can reach each other.
#
# Contracts asserted here are the real ones, verified against the source:
#   - POST http://operations-api:8000/internal/v1/opportunities
#       requires header X-Internal-Service-Token  (403 otherwise)
#       requires header X-Idempotency-Key         (400 otherwise)
#       body is OpportunityCommandV1: slot and user_id are mandatory
#       a replayed idempotency key returns 200 {"status":"duplicate"}
#   - GET  http://marketing-api:8000/internal/v1/operational-summary
#       requires header X-Internal-Service-Token  (403 otherwise)
#
# The durable outbox is JSON-backed at $DATA_DIR/cross_project_outbox.json in
# each service, and the inbox dedupe store is $DATA_DIR/service_inbox.json.
# The PostgreSQL cross_project_* tables exist in the Operations schema but the
# runtime does not write them, so asserting against them would pass vacuously.
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

# HTTP from inside a container. Prints "<status> <body>".
incurl() {
  local svc="$1"; shift
  $COMPOSE exec -T "$svc" python - "$@" <<'PY'
import sys, json, urllib.request, urllib.error
method, url = sys.argv[1], sys.argv[2]
token, idem, body = sys.argv[3], sys.argv[4], sys.argv[5]
req = urllib.request.Request(url, method=method)
if token: req.add_header("X-Internal-Service-Token", token)
if idem:  req.add_header("X-Idempotency-Key", idem)
data = None
if body:
    data = body.encode()
    req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, data=data, timeout=20) as r:
        print(r.status, r.read(400).decode("utf-8", "replace").replace("\n", " "))
except urllib.error.HTTPError as e:
    print(e.code, e.read(400).decode("utf-8", "replace").replace("\n", " "))
except Exception as e:                       # noqa: BLE001 - surfaced as a failure
    print("ERR", type(e).__name__)
PY
}

# Run python inside a container with the app importable.
inpy() { local svc="$1"; shift; $COMPOSE exec -T -w /app "$svc" python -c "$1"; }

wait_healthy() {
  local svc="$1" tries=90
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

OPP='{"slot":"account1","user_id":900001,"opportunity_type":"partnership","name":"Split Test","summary":"dual-service verification"}'

step "Bring up the isolated dual-service stack"
$COMPOSE up -d --build || { echo "compose up failed"; exit 1; }

step "1-2. Both services healthy"
wait_healthy marketing-api && ok "Marketing container healthy" || bad "Marketing container healthy"
wait_healthy operations-api && ok "Operations container healthy" || bad "Operations container healthy"

step "3-4. Both databases ready"
$COMPOSE exec -T marketing-db pg_isready -U marketing -d marketing >/dev/null 2>&1 \
  && ok "Marketing PostgreSQL ready" || bad "Marketing PostgreSQL ready"
$COMPOSE exec -T operations-db pg_isready -U operations -d operations >/dev/null 2>&1 \
  && ok "Operations PostgreSQL ready" || bad "Operations PostgreSQL ready"

step "5-6. Migrations applied inside the containers"
m=$(inpy marketing-api "
import psycopg2,os
c=psycopg2.connect(os.environ['DATABASE_URL']);cur=c.cursor()
cur.execute(\"select count(*) from information_schema.tables where table_schema='public'\")
print(cur.fetchone()[0])" 2>/dev/null | tr -d '[:space:]')
[ "${m:-0}" -ge 2 ] && ok "Marketing schema present ($m tables)" || bad "Marketing schema present" "tables=${m:-none}"
o=$(inpy operations-api "
import psycopg2,os
c=psycopg2.connect(os.environ['DATABASE_URL']);cur=c.cursor()
cur.execute(\"select count(*) from information_schema.tables where table_schema='public'\")
print(cur.fetchone()[0])" 2>/dev/null | tr -d '[:space:]')
[ "${o:-0}" -ge 30 ] && ok "Operations schema present ($o tables)" || bad "Operations schema present" "tables=${o:-none}"

step "7. No peer address is a container-local loopback"
peers=$($COMPOSE exec -T marketing-api printenv OPERATIONS_INTERNAL_URL 2>/dev/null; \
        $COMPOSE exec -T operations-api printenv MESSAGING_INTERNAL_URL 2>/dev/null)
if printf '%s' "$peers" | grep -q '127\.0\.0\.1\|localhost'; then
  bad "Peer URLs use service discovery" "found loopback: $peers"
else
  ok "Peer URLs use service discovery ($(printf '%s' "$peers" | tr '\n' ' '))"
fi

step "8. Marketing -> Operations authenticated contract"
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "dst-key-1" "$OPP")
case "$r" in 200*ok*) ok "Marketing reached operations-api ($r)";; *) bad "Marketing -> Operations" "got $r";; esac

step "9. Operations -> Marketing authenticated contract"
r=$(incurl operations-api GET http://marketing-api:8000/internal/v1/operational-summary "$TOKEN" "" "")
case "$r" in 200*) ok "Operations reached marketing-api ($r)";; *) bad "Operations -> Marketing" "got $r";; esac

step "10. Unauthenticated and bad-token peer calls are rejected"
r=$(incurl operations-api GET http://marketing-api:8000/internal/v1/operational-summary "" "" "")
case "$r" in 401*|403*) ok "Unauthenticated peer rejected ($r)";; *) bad "Unauthenticated peer rejected" "got $r";; esac
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "wrong-token" "dst-bad" "$OPP")
case "$r" in 401*|403*) ok "Bad-token peer rejected ($r)";; *) bad "Bad-token peer rejected" "got $r";; esac

step "11. A missing idempotency key is refused"
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "" "$OPP")
case "$r" in 400*) ok "Missing idempotency key refused ($r)";; *) bad "Missing idempotency key refused" "got $r";; esac

step "12. Duplicate replay is idempotent"
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "dst-key-1" "$OPP")
case "$r" in *duplicate*) ok "Replayed key answered duplicate ($r)";; *) bad "Duplicate replay idempotent" "got $r";; esac

step "13. Durable outbox delivers while the peer is up"
inpy marketing-api "
from services import cross_project_outbox as o
o.enqueue(event_type='opportunity', idempotency_key='dst-out-1', payload=$OPP)
print('enqueued')" >/dev/null 2>&1
d=$(inpy marketing-api "
from services.opportunity_event_producer import dispatch_opportunities_once as d
print(d())" 2>/dev/null)
pend=$(inpy marketing-api "
from services import cross_project_outbox as o
print(sum(1 for r in o._load() if r.get('delivered_at') is None))" 2>/dev/null | tr -d '[:space:]')
[ "${pend:-1}" = "0" ] && ok "Outbox drained (pending=0)" || bad "Outbox drained" "pending=${pend:-unknown} dispatch=$d"

step "14-16. Operations outage: Marketing survives, work queues, then recovers"
$COMPOSE stop operations-api >/dev/null 2>&1
inpy marketing-api "
from services import cross_project_outbox as o
o.enqueue(event_type='opportunity', idempotency_key='dst-outage-1', payload=$OPP)
from services.opportunity_event_producer import dispatch_opportunities_once as d
d()" >/dev/null 2>&1
h=$(inpy marketing-api "
import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status)" 2>/dev/null | tr -d '[:space:]')
[ "$h" = "200" ] && ok "Marketing healthy while Operations is down" || bad "Marketing survives peer outage" "health=$h"
pend=$(inpy marketing-api "
from services import cross_project_outbox as o
print(sum(1 for r in o._load() if r.get('idempotency_key')=='dst-outage-1' and r.get('delivered_at') is None))" 2>/dev/null | tr -d '[:space:]')
[ "${pend:-0}" = "1" ] && ok "Undelivered work retained for retry" || bad "Outbox retains undelivered work" "pending=${pend:-unknown}"

$COMPOSE start operations-api >/dev/null 2>&1
wait_healthy operations-api && ok "Operations recovered" || bad "Operations recovered"
inpy marketing-api "
from services.opportunity_event_producer import dispatch_opportunities_once as d
d()" >/dev/null 2>&1
pend=$(inpy marketing-api "
from services import cross_project_outbox as o
print(sum(1 for r in o._load() if r.get('delivered_at') is None))" 2>/dev/null | tr -d '[:space:]')
[ "${pend:-1}" = "0" ] && ok "Queued delivery recovered after peer restart" || bad "Delivery recovers" "pending=${pend:-unknown}"

step "17. Recovery caused no duplicate side effect"
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "dst-outage-1" "$OPP")
case "$r" in *duplicate*) ok "Recovered delivery was not applied twice ($r)";; *) bad "No duplicate side effects" "got $r";; esac

step "18-19. Marketing outage: Operations survives and recovers"
$COMPOSE stop marketing-api >/dev/null 2>&1
h=$(inpy operations-api "
import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status)" 2>/dev/null | tr -d '[:space:]')
[ "$h" = "200" ] && ok "Operations healthy while Marketing is down" || bad "Operations survives peer outage" "health=$h"
$COMPOSE start marketing-api >/dev/null 2>&1
wait_healthy marketing-api && ok "Marketing recovered" || bad "Marketing recovered"

step "20-22. State survives a full restart"
$COMPOSE restart >/dev/null 2>&1
wait_healthy marketing-api && wait_healthy operations-api \
  && ok "Both services healthy after restart" || bad "Both services healthy after restart"
inbox=$(inpy operations-api "
import json,os
from core.config import DATA_DIR
p=os.path.join(DATA_DIR,'service_inbox.json')
print(len(json.load(open(p))) if os.path.exists(p) else 0)" 2>/dev/null | tr -d '[:space:]')
[ "${inbox:-0}" -ge 1 ] && ok "Inbox idempotency state survived restart ($inbox keys)" \
  || bad "Inbox state survives restart" "keys=${inbox:-unknown}"
outbox=$(inpy marketing-api "
from services import cross_project_outbox as o
print(len(o._load()))" 2>/dev/null | tr -d '[:space:]')
[ "${outbox:-0}" -ge 1 ] && ok "Outbox state survived restart ($outbox rows)" \
  || bad "Outbox state survives restart" "rows=${outbox:-unknown}"
rows=$(inpy operations-api "
import psycopg2,os
c=psycopg2.connect(os.environ['DATABASE_URL']);cur=c.cursor()
cur.execute('select count(*) from operations_schema_migrations');print(cur.fetchone()[0])" 2>/dev/null | tr -d '[:space:]')
[ "${rows:-0}" -ge 27 ] && ok "Operations database survived restart ($rows migrations)" \
  || bad "Database survives restart" "migrations=${rows:-unknown}"

step "23. Replay still idempotent after restart"
r=$(incurl marketing-api POST http://operations-api:8000/internal/v1/opportunities "$TOKEN" "dst-key-1" "$OPP")
case "$r" in *duplicate*) ok "Idempotency survived restart ($r)";; *) bad "Idempotency survives restart" "got $r";; esac

printf '\n===== dual-service result: %d passed, %d failed =====\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
