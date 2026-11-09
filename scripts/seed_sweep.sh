#!/usr/bin/env bash
# Robustness check: re-run a public scenario under different seeds (same truth, different randomness).
# The controller never sees these files; they only drive the simulator.
# usage: scripts/seed_sweep.sh <scenario> <controller_dir> <tag> <port> seed...
set -euo pipefail
N=$1; CTL=$2; TAG=$3; P=$4; shift 4; PY=$PWD/.venv/bin/python; OUT=runs/sweep; mkdir -p $OUT
for SEED in "$@"; do
  Y=$OUT/${N}_s$SEED.yaml
  sed "s/^seed: .*/seed: $SEED/" scenarios/$N.yaml > $Y
  [ -d $OUT/base_${N}_s$SEED ] || $PY -m sim baseline --scenario $Y --run-dir $OUT/base_${N}_s$SEED > /dev/null
  pkill -f "port $P" 2>/dev/null || true; sleep 1
  $PY -m sim serve --scenario $Y --run-dir $OUT/${TAG}_${N}_s$SEED --force --port $P > /dev/null 2>&1 &
  for i in $(seq 120); do curl -s localhost:$P/health >/dev/null && break; sleep 1; done
  (cd $CTL && PYTHON=$PY ./run.sh http://127.0.0.1:$P --db /tmp/sweep_${TAG}.db > /tmp/sweep_${TAG}_${N}_$SEED.log 2>&1); rm -f /tmp/sweep_${TAG}.db*
  $PY -m sim grade --run $OUT/${TAG}_${N}_s$SEED --baseline $OUT/base_${N}_s$SEED --json > $OUT/${TAG}_${N}_s$SEED.json
  $PY -c "
import json; g=json.load(open('$OUT/${TAG}_${N}_s$SEED.json')); v=g['controller_changes']['verdicts']
print('$TAG $N seed $SEED  conclusion %+.1f%%  harmful %d  beneficial %d  rollbacks %d' % (g['delta_vs_baseline']['ALL']['conclusion_rate']*100, v.get('harmful',0), v.get('beneficial',0), open('/tmp/sweep_${TAG}_${N}_$SEED.log').read().count('decay_detected')))"
  pkill -f "port $P" || true
done
