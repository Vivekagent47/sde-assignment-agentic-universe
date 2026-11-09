"""SQLite state: aggregates, proposals, audit log, metrics.

Calls are folded into counters at ingest time; raw events are not kept. Each chunk is
applied in one transaction together with the cursor, so a crash mid-chunk replays it
cleanly and a finished chunk is never counted twice.

Segment = age_band x city_tier, packed in one byte per person (255 = unknown). Cohorts
are sets of segments, so detection, experiments and the supervisor view all sum the
same counters.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date

AGES = ["18-30", "31-45", "46-60", "60+"]
TIERS = ["1", "2", "3"]
D0 = date(2027, 1, 4)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS segmap(wf TEXT PRIMARY KEY, blob BLOB);
CREATE TABLE IF NOT EXISTS pk(wf TEXT, day INT, hour INT, seg INT, did TEXT, first INT, dials INT, conn INT,
    PRIMARY KEY(wf, day, hour, seg, did, first)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS en(wf TEXT, day INT, hour INT, seg INT, wm TEXT, voice TEXT, prompt TEXT, ver INT,
    conn INT, eng INT, flag INT, PRIMARY KEY(wf, day, hour, seg, wm, voice, prompt, ver)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS leads_day(wf TEXT, day INT, leads INT, PRIMARY KEY(wf, day));
CREATE TABLE IF NOT EXISTS cfg_events(event_id INT PRIMARY KEY, wf TEXT, ts TEXT, version INT, actor TEXT,
    dims TEXT, proposal_id TEXT, approval_id TEXT);
CREATE TABLE IF NOT EXISTS proposals(pid TEXT PRIMARY KEY, wf TEXT, kind TEXT, path TEXT, status TEXT,
    base_version INT, approval_id TEXT, applied_version INT, created INT, updated INT, reserve INT,
    body TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, minute INT, wf TEXT, kind TEXT,
    summary TEXT, data TEXT, pid TEXT);
CREATE TABLE IF NOT EXISTS metrics(minute INT PRIMARY KEY, wall REAL, events INT, ingest_s REAL,
    decide_s REAL, credits REAL, rss_mb REAL);
"""

_days: dict[str, int] = {}


def day_of(ts: str) -> int:
    d = ts[:10]
    v = _days.get(d)
    if v is None:
        v = _days[d] = (date.fromisoformat(d) - D0).days
    return v


def minute_of(ts: str) -> int:
    return day_of(ts) * 1440 + int(ts[11:13]) * 60 + int(ts[14:16])


def fmt_min(m: int) -> str:
    d, r = divmod(m, 1440)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def seg_age(seg: int) -> int:
    return seg // 3 if seg != 255 else -1


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.segmaps: dict[str, bytearray] = {wf: bytearray(b) for wf, b in self.db.execute("SELECT wf, blob FROM segmap")}
        self.now = 0

    # ---------------------------------------------------------------- meta / audit
    def get(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(v)))

    def decide(self, wf: str | None, kind: str, summary: str, data=None, pid: str | None = None):
        self.db.execute("INSERT INTO decisions(minute, wf, kind, summary, data, pid) VALUES(?,?,?,?,?,?)",
                        (self.now, wf, kind, summary, json.dumps(data or {}, default=str), pid))
        print(f"[{fmt_min(self.now)}] {wf or '-'} {kind}: {summary}", flush=True)

    def q(self, sql: str, *args):
        return self.db.execute(sql, args).fetchall()

    # ---------------------------------------------------------------- ingest
    def ingest(self, chunk: int, text: str) -> int:
        pk = defaultdict(lambda: [0, 0])
        en = defaultdict(lambda: [0, 0, 0])
        leads = defaultdict(int)
        cfg, touched = [], set()
        loads = json.loads
        n = 0
        for line in text.splitlines():
            if not line:
                continue
            n += 1
            ev = loads(line)
            t = ev["type"]
            if t == "call":
                wf = ev["workflow_id"]
                pi = int(ev["person_id"].rsplit(":p", 1)[1])
                sm = self.segmaps.get(wf)
                seg = sm[pi] if sm is not None and pi < len(sm) else 255
                ts = ev["ts"]
                day, hour = day_of(ts), int(ts[11:13])
                v = ev["variant"]
                a = pk[(wf, day, hour, seg, v["caller_id"], 1 if ev["attempt_no"] == 1 else 0)]
                a[0] += 1
                if ev["connected"]:
                    a[1] += 1
                    e = en[(wf, day, hour, seg, v["welcome_message"], v["voice"], v["prompt_version"],
                            ev["config_version"])]
                    e[0] += 1
                    e[1] += ev["engaged"]
                    e[2] += bool(ev["qa"] and ev["qa"]["hallucination_flag"])
            elif t == "lead":
                wf = ev["workflow_id"]
                pi = int(ev["person_id"].rsplit(":p", 1)[1])
                f = ev["features"]
                try:
                    seg = AGES.index(f["age_band"]) * 3 + TIERS.index(f["city_tier"])
                except ValueError:
                    seg = 255
                sm = self.segmaps.setdefault(wf, bytearray())
                if pi >= len(sm):
                    sm.extend(b"\xff" * (pi + 1 - len(sm) + 4096))
                sm[pi] = seg
                touched.add(wf)
                leads[(wf, day_of(ev["ts"]))] += 1
            elif t == "config_changed":
                cfg.append((ev["event_id"], ev["workflow_id"], ev["ts"], ev["version"], ev["actor"],
                            json.dumps(ev["changed_dims"]), ev.get("proposal_id"), ev.get("approval_id")))
        db = self.db
        db.execute("BEGIN")
        try:
            db.executemany("INSERT INTO pk VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                           "dials=dials+excluded.dials, conn=conn+excluded.conn",
                           [(*k, *v) for k, v in pk.items()])
            db.executemany("INSERT INTO en VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                           "conn=conn+excluded.conn, eng=eng+excluded.eng, flag=flag+excluded.flag",
                           [(*k, *v) for k, v in en.items()])
            db.executemany("INSERT INTO leads_day VALUES(?,?,?) ON CONFLICT DO UPDATE SET leads=leads+excluded.leads",
                           [(*k, v) for k, v in leads.items()])
            db.executemany("INSERT OR IGNORE INTO cfg_events VALUES(?,?,?,?,?,?,?,?)", cfg)
            db.executemany("INSERT OR REPLACE INTO segmap VALUES(?,?)",
                           [(wf, bytes(self.segmaps[wf])) for wf in touched])
            db.execute("INSERT OR REPLACE INTO meta VALUES('cursor', ?)", (json.dumps(chunk),))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            # in-memory segmaps may now be ahead of disk; harmless (same values are re-derived on replay)
            raise
        return n
