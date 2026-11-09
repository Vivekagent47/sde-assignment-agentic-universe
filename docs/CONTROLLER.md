# Controller runbook

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt      # Python >= 3.10
.venv/bin/python -m sim serve --scenario scenarios/public_02_incidents.yaml --run-dir runs/mine --force &
./run.sh http://127.0.0.1:8700            # runs to scenario end; state in state/<scenario>.db
scripts/run_and_grade.sh public_02_incidents 8702   # fresh sim + controller + reports/<name>.md (needs runs/base_<name>)
scripts/crash_test.sh public_02_incidents 8710      # random kill -9 loop + consistency check
scripts/seed_sweep.sh public_02_incidents . mytag 8721 202 11 12   # same scenario, other seeds: robustness check
.venv/bin/python -m pytest -q tests
```
Restarting `./run.sh` against the same simulator resumes. If the simulator itself was restarted (new world),
the old state file is moved aside automatically (`*.stale-<ts>`).

## Debug
| Question | Command |
|---|---|
| Why did workflow X change at T? | `python -m controller.report why --db state/S.db --workflow X --at "D3 14:00"` |
| What's being tested / waiting on the client? | `python -m controller.report view --db state/S.db` |
| Is the loop healthy (ingest rate, decide time, RSS, credits)? | `python -m controller.report loop --db state/S.db` |
| Raw decision log | `sqlite3 state/S.db "select minute, wf, kind, summary from decisions order by id"` |
| Proposal lifecycle | `sqlite3 state/S.db "select pid, status, approval_id, applied_version from proposals"` |

Every decision is also printed to stdout as `[D3 11:45] wf kind: summary`.

## Code map (≈ 1,000 lines)
| File | Owns |
|---|---|
| `controller/__main__.py` | loop: ingest → poll → decide → advance; tick sizing; new-world detection |
| `controller/store.py` | SQLite schema, chunk ingest (one txn per chunk + cursor), segment map |
| `controller/gov.py` | proposal state machine, budget/timing rules, apply, crash reconcile |
| `controller/brain.py` | per-workflow decisions: edits, incidents, caller IDs, decay, daily batch, experiments |
| `controller/util.py` | config diff / auto-apply rule mirror, z-test, Poisson tail |
| `controller/report.py` | why / view / loop read-only views |

## Extend
- **New detector:** add a `_step(wf, s, cfg, ver) -> bool` to `Brain` and put it in the `tick` tuple. Return True if it
  changed config (the loop re-reads config). Read counters with `self.st.q(...)`. Log with `self.st.decide(...)`.
  Change config only through `self.gov.auto(...)` or `self._request(...)`.
- **Finer cohorts:** widen the segment byte in `store.py` (`AGES`, `TIERS`) and extend `COHORTS` in `brain.py`.
  Existing counters stay valid; segments are recomputed per lead.
- **Thresholds** are module constants at the top of `brain.py`, each with the reasoning next to it.
