"""Controller entry point: python -m controller --url http://127.0.0.1:8700 [--db state/x.db]

Loop: ingest every chunk after the cursor -> reconcile/poll approvals -> decide -> advance.
Restart-safe: all state is in SQLite; the simulator holds the clock.
"""
from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from pathlib import Path

from .api import Sim
from .brain import Brain
from .gov import Gov
from .store import Store, fmt_min


def ingest_new(sim: Sim, st: Store) -> int:
    cur = st.get("cursor", -1)
    chunks = sim.get("/events/chunks", kind="history", after=cur) + sim.get("/events/chunks", kind="events", after=cur)
    n = 0
    for ch in sorted(chunks, key=lambda c: c["chunk"]):
        n += st.ingest(ch["chunk"], sim.text(f"/events/chunks/{ch['chunk']}"))
    return n


def active_range(wfs: dict) -> tuple[int, int]:
    """Hours in which we tick every 15 min: dialling, approver hours and apply windows."""
    lo = min(min(w["dialling_window"][0], w["governance"]["approver_hours"][0]) for w in wfs.values())
    hi = max(max(w["dialling_window"][1], w["governance"]["approver_hours"][1] + w["governance"]["apply_window_hours"])
             for w in wfs.values())
    return lo - 1, min(hi, 24)


def next_step(now: int, lo: int, hi: int) -> int:
    mod = now % 1440
    if lo * 60 <= mod < hi * 60:
        return 15
    if mod >= hi * 60:
        return 1440 - mod + lo * 60
    return lo * 60 - mod


def open_store(sim: Sim, db: str | None) -> Store:
    st = sim.get("/sim/state")
    path = Path(db or f"state/{st['scenario']}.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    s = Store(str(path))
    seen = s.get("last_minute")
    if s.get("scenario") not in (None, st["scenario"]) or (seen is not None and seen > st["sim_minute"]) \
            or s.get("cursor", -1) >= st["chunks"]:
        # The simulator restarted, so this is a new world: old state describes a world that no longer exists.
        s.db.close()
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                p.rename(f"{p}.stale-{stamp}")
        print(f"state in {path} belongs to another world; moved aside", flush=True)
        s = Store(str(path))
    s.put("scenario", st["scenario"])
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controller")
    ap.add_argument("--url", default="http://127.0.0.1:8700")
    ap.add_argument("--db")
    ap.add_argument("--stop-at", type=int, help="exit after this sim minute (for crash tests)")
    a = ap.parse_args(argv)
    sim = Sim(a.url)
    st = open_store(sim, a.db)
    wfs = {w["workflow_id"]: w for w in sim.get("/workflows")}
    gov = Gov(sim, st, wfs)
    brain = Brain(sim, st, gov, wfs)
    lo, hi = active_range(wfs)
    t_start = time.time()
    state = sim.get("/sim/state")
    st.now = state["sim_minute"]
    gov.reconcile()
    while True:
        t0 = time.time()
        n = ingest_new(sim, st)
        t1 = time.time()
        state = sim.get("/sim/state")
        st.now = state["sim_minute"]
        gov.poll()
        brain.tick(state["credits_remaining"])
        gov.poll()
        t2 = time.time()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
        st.db.execute("INSERT OR REPLACE INTO metrics VALUES(?,?,?,?,?,?,?)",
                      (st.now, time.time(), n, t1 - t0, t2 - t1, state["credits_remaining"], rss))
        st.put("last_minute", st.now)
        if state["finished"] or (a.stop_at is not None and st.now >= a.stop_at):
            break
        sim.advance(next_step(st.now, lo, hi), st.now)
    print(f"done at {fmt_min(st.now)} in {time.time() - t_start:.0f}s; proposals: {gov.summary()}; "
          f"credits left {state['credits_remaining']}", flush=True)


if __name__ == "__main__":
    main()
