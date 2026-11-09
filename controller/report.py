"""Read-only views over the controller's own store (never the simulator's logs).

  python -m controller.report why  --db state/X.db --workflow ks_cancel --at "D3 14:00"
  python -m controller.report view --db state/X.db [--workflow ks_cancel]
  python -m controller.report loop --db state/X.db          # loop health: ingest, lag, decisions, credits
"""
from __future__ import annotations

import argparse
import json
import sqlite3

from .store import fmt_min


def parse_when(s: str) -> int:
    d, hm = s.split()
    h, m = hm.split(":")
    return int(d.lstrip("Dd")) * 1440 + int(h) * 60 + int(m)


def why(db, wf: str, at: int):
    """The config change in effect at `at`: what changed, why, who approved, what happened next."""
    row = db.execute("SELECT pid, kind, path, status, approval_id, applied_version, body, updated FROM proposals "
                     "WHERE wf=? AND status='applied' AND updated<=? ORDER BY updated DESC LIMIT 1", (wf, at)).fetchone()
    sup = db.execute("SELECT ts, version, dims FROM cfg_events WHERE wf=? AND actor='supervisor' ORDER BY event_id", (wf,)).fetchall()
    if not row:
        print(f"{wf}: no controller change applied by {fmt_min(at)} (business default or supervisor config)")
    else:
        pid, kind, path, status, aid, ver, body, applied = row
        b = json.loads(body)
        print(f"# {wf} at {fmt_min(at)}: config v{ver}, applied {fmt_min(applied)} by the controller")
        print(f"proposal {pid} ({kind}) via {'auto-apply within policy' if path == 'auto' else f'approval {aid} (client approver)'}")
        print(f"summary   : {b.get('change_summary', b.get('rationale'))}")
        print(f"rationale : {b.get('rationale')}")
        print(f"diff      : {json.dumps(b.get('diff', {}))[:600]}")
        if "impact_score" in b:
            print(f"impact {b['impact_score']} · affected leads {b['affected_leads']} · evidence {b.get('evidence')}")
            print(f"dry run   : {json.dumps(b['dry_run'])[:600]}")
    print("\n## decision log around it (24h before -> 48h after)")
    for m, kind, summary, pid in db.execute("SELECT minute, kind, summary, pid FROM decisions WHERE wf=? AND minute "
                                            "BETWEEN ? AND ? ORDER BY id", (wf, at - 1440, at + 2880)):
        print(f"{fmt_min(m)}  {kind:20s} {summary[:200]}")
    if sup:
        print("\n## supervisor edits (bypass governance):", ", ".join(f"v{v} {ts[:16]} {d}" for ts, v, d in sup))


def view(db, only: str | None):
    """One screen per workflow: what's being tested, what's winning, what we want, what changed today."""
    for (wf,) in db.execute("SELECT DISTINCT wf FROM pk ORDER BY wf"):
        if only and wf != only:
            continue
        s = json.loads((db.execute("SELECT v FROM meta WHERE k=?", (f"wf:{wf}",)).fetchone() or ["{}"])[0])
        now = json.loads((db.execute("SELECT v FROM meta WHERE k='last_minute'").fetchone() or ["0"])[0])
        print(f"\n=== {wf}  @ {fmt_min(now)}")
        for dim, e in (s.get("exp") or {}).items():
            print(f"  testing {dim:16s} {e.get('status')}  incumbent={e.get('inc')} retired={e.get('retired', [])}")
        for r in s.get("records", []):
            if r["status"] in ("active", "rolling_back", "rolled_back"):
                print(f"  winner  {r['dim']}={r['champ']} for {r['cids']} (holdout {r['hold']}, lift at promotion "
                      f"{r['d']:+.3f}) -> {r['status']}")
        if s.get("bad_dids"):
            print(f"  caller IDs rotated out: {s['bad_dids']}")
        if s.get("incident"):
            print(f"  INCIDENT: {s['incident']}")
        for pid, kind, status, aid in db.execute("SELECT pid, kind, status, approval_id FROM proposals WHERE wf=? AND "
                                                 "status IN ('pending','approved')", (wf,)):
            print(f"  WAITING ON YOU: {aid} {kind} ({status})")
        day0 = now - now % 1440
        for m, kind, summary in db.execute("SELECT minute, kind, summary FROM decisions WHERE wf=? AND minute>=? AND kind IN "
                                           "('auto_applied','approval_applied','supervisor_edit','incident_detected',"
                                           "'did_degraded','decay_detected') ORDER BY id", (wf, day0)):
            print(f"  today {fmt_min(m)} {kind}: {summary[:120]}")


def loop(db):
    rows = db.execute("SELECT minute, wall, events, ingest_s, decide_s, credits, rss_mb FROM metrics ORDER BY minute").fetchall()
    if not rows:
        return
    ev = sum(r[2] for r in rows)
    ing = sum(r[3] for r in rows)
    dec = sum(r[4] for r in rows)
    days = {}
    for m, wall, *_ in rows:
        days.setdefault(m // 1440, []).append(wall)
    print(f"ticks {len(rows)} · events {ev:,} · ingest {ing:.0f}s ({ev / max(ing, 1e-9):,.0f} ev/s) · decide {dec:.0f}s · "
          f"peak RSS {max(r[6] for r in rows):.0f} MB · credits left {rows[-1][5]}")
    print(f"decide time per tick: mean {dec / len(rows):.2f}s, max {max(r[4] for r in rows):.2f}s")
    walls = sorted(days.items())
    for (d, w), nxt in zip(walls, walls[1:] + [(None, [rows[-1][1]])]):
        print(f"  D{d}: {nxt[1][0] - w[0]:.0f}s wall (sim + controller)")
    n = db.execute("SELECT kind, COUNT(*) FROM decisions GROUP BY kind ORDER BY 2 DESC").fetchall()
    print("decisions:", ", ".join(f"{k}={c}" for k, c in n))


def main():
    ap = argparse.ArgumentParser(prog="controller.report")
    ap.add_argument("cmd", choices=["why", "view", "loop"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--workflow")
    ap.add_argument("--at")
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    if a.cmd == "why":
        why(db, a.workflow, parse_when(a.at))
    elif a.cmd == "view":
        view(db, a.workflow)
    else:
        loop(db)


if __name__ == "__main__":
    main()
