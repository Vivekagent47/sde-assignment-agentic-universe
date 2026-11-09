#!/usr/bin/env bash
# usage: scripts_run.sh <scenario-name> <port>  — fresh sim + controller + grade
set -euo pipefail
N=$1; P=$2; PY=.venv/bin/python
pkill -f "port $P" 2>/dev/null || true; sleep 1
rm -f state/$N.db*
$PY -m sim serve --scenario scenarios/$N.yaml --run-dir runs/mine_$N --force --port $P > /tmp/sim_$N.log 2>&1 &
for i in $(seq 120); do curl -s localhost:$P/health >/dev/null && break; sleep 1; done
./run.sh http://127.0.0.1:$P > /tmp/ctl_$N.log 2>&1
mkdir -p reports && $PY -m sim grade --run runs/mine_$N --baseline runs/base_$N --out reports/$N.md > /dev/null
pkill -f "port $P" || true
