#!/usr/bin/env bash
# Kill -9 the controller at random points until the scenario finishes, then check nothing was
# double-applied or lost. usage: scripts/crash_test.sh [scenario] [port]
set -uo pipefail
N=${1:-public_01_dev}; P=${2:-8710}; PY=.venv/bin/python; LOG=reports/crash_test_$N.log
mkdir -p reports; : > "$LOG"
pkill -f "port $P" 2>/dev/null; sleep 1; rm -f state/crash_$N.db*
$PY -m sim serve --scenario scenarios/$N.yaml --run-dir runs/crash_$N --force --port $P > /tmp/sim_crash.log 2>&1 &
for i in $(seq 120); do curl -s localhost:$P/health >/dev/null && break; sleep 1; done
kills=0
while :; do
  ./run.sh http://127.0.0.1:$P --db state/crash_$N.db >> "$LOG" 2>&1 &
  pid=$!
  sleep "$(awk -v s=$RANDOM 'BEGIN{srand(s); printf "%.1f", 0.5 + rand()*2.5}')"
  if kill -0 $pid 2>/dev/null; then
    kill -9 $pid; wait $pid 2>/dev/null; kills=$((kills+1))
    echo "=== KILL -9 #$kills at $(curl -s localhost:$P/sim/state | $PY -c 'import json,sys; print(json.load(sys.stdin)["ts"])')" >> "$LOG"
  else
    wait $pid; break
  fi
done
echo "=== finished after $kills kills" >> "$LOG"
$PY - "$P" "$N" >> "$LOG" <<'PY'
import collections, httpx, sqlite3, sys
p, n = sys.argv[1], sys.argv[2]
c = httpx.Client(base_url=f"http://127.0.0.1:{p}")
db = sqlite3.connect(f"state/crash_{n}.db")
srv = {a["proposal_id"]: a for a in c.get("/approvals").json()}
loc = dict(db.execute("SELECT pid, status FROM proposals WHERE path='approval'"))
problems = [f"{pid}: server={a['status']} local={loc.get(pid)}" for pid, a in srv.items()
            if pid not in loc or (a["status"] in ("applied", "pending", "approved") and loc[pid] != a["status"])]
for w in c.get("/workflows").json():
    pids = [v["proposal_id"] for v in c.get(f"/workflows/{w['workflow_id']}/config/history").json() if v["proposal_id"]]
    dup = [k for k, v in collections.Counter(pids).items() if v > 1]
    problems += [f"double-applied {d}" for d in dup]
print("server approvals:", len(srv), "local:", len(loc), "statuses:", dict(collections.Counter(a["status"] for a in srv.values())))
print("CHECK", "PASS" if not problems else "FAIL", problems)
PY
pkill -f "port $P"
tail -3 "$LOG"
