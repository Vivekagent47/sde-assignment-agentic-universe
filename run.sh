#!/usr/bin/env bash
# ./run.sh <SIM_URL> — runs the controller until the scenario finishes. No Docker needed:
# Python >= 3.10 with httpx (pip install -r requirements.txt). State goes to state/<scenario>.db.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
exec "$PY" -m controller --url "$SIM_URL" "${@:2}"
