"""Decision logic. Everything here reads the SQLite counters; nothing reads simulator internals.

Per workflow, per tick:
  1. observe supervisor edits      -> freeze touched dims 48h, watch prompt changes
  2. hallucination incident        -> validate, revert to a known-good prompt via approval (reserve slot)
  3. caller-ID health              -> drop flagged DIDs, spread load evenly over healthy ones (auto if policy allows)
  4. winner decay                  -> roll back a promoted variant that stops beating its holdout
  5. daily batch (one approval)    -> cohorts + call-time schedule + DID spread + experiment moves

Cohorts are the four age bands. They are the one shared cohort object: the schedule,
the allocations, the experiment read-outs and the supervisor view all use these IDs.
"""
from __future__ import annotations

import copy
import math

from .api import Sim, SimError
from .gov import Gov
from .store import AGES, Store, fmt_min
from .util import changed_dims, diff, dist, poisson_sf, tv, z_two_prop

COHORTS = [{"id": "age_" + a.replace("-", "_").replace("+", "p"), "where": {"age_band": [a]}} for a in AGES]
CID = [c["id"] for c in COHORTS]

EXPLORE_ALT = 0.15        # max share per challenger arm; incumbent keeps >= 55%
MIN_N_DROP, Z_DROP = 150, -2.0      # fast to stop harm
MIN_N_WIN, Z_WIN, MIN_LIFT = 300, 2.8, 0.03   # slow to promote (z 2.8 ~ Bonferroni over arms x daily looks)
MIN_N_COH, Z_COH = 150, 3.0         # cohort-only winner needs stronger evidence (4 cohorts x arms)
HOLDOUT = 0.2
DID_PROBE = {True: 0.05, False: 0.10}  # unknown caller IDs: auto-capable workflows probe smaller, more often
DID_BAD_RATIO, DID_BAD_Z = 0.65, -3.0
FREEZE_MIN = 48 * 60
CREDIT_FLOOR = 20                 # below this, only incident work may spend credits


class Brain:
    def __init__(self, sim: Sim, st: Store, gov: Gov, wfs: dict):
        self.sim, self.st, self.gov, self.wfs = sim, st, gov, wfs
        self.credits = 0.0

    @property
    def now(self) -> int:
        return self.st.now

    # ---------------------------------------------------------------- state
    def _state(self, wf: str) -> dict:
        s = self.st.get(f"wf:{wf}")
        if s is None:
            s = {"last_ev": 0, "frozen": {}, "exp": {}, "records": [], "bad_dids": {}, "incident": None,
                 "watch": None, "good_prompts": [], "prompt": None, "validations": {}}
        return s

    def _cfg(self, wf: str):
        c = self.sim.get(f"/workflows/{wf}/config")
        return c["config"], c["version"]

    def tick(self, credits: float):
        self.credits = credits
        for wf in self.wfs:
            s = self._state(wf)
            cfg, ver = self._cfg(wf)
            self._bootstrap(wf, s, cfg)
            self._observe_edits(wf, s, cfg)
            self._track(wf, s, cfg, ver)
            self.st.put(f"wf:{wf}", s)
            for step in (self._incident, self._caller_ids, self._decay, self._batch):
                if step(wf, s, cfg, ver):
                    cfg, ver = self._cfg(wf)
                    self._track(wf, s, cfg, ver)
                self.st.put(f"wf:{wf}", s)

    # ---------------------------------------------------------------- 0. bootstrap from history
    def _bootstrap(self, wf, s, cfg):
        if s["prompt"] is not None:
            return
        p = cfg["prompt_version"]
        conn, flags = self.st.q("SELECT COALESCE(SUM(conn),0), COALESCE(SUM(flag),0) FROM en WHERE wf=? AND prompt=?",
                                wf, p)[0]
        s["prompt"] = p
        s["good_prompts"] = [p]
        s["base_flag"] = (flags + 1) / (conn + 100)  # history flag rate of the production prompt
        self.st.decide(wf, "bootstrap", f"history: prompt {p} flag rate {s['base_flag']:.4f} over {conn} connects",
                       {"conn": conn, "flags": flags})

    # ---------------------------------------------------------------- 1. supervisor edits
    def _observe_edits(self, wf, s, cfg):
        for eid, ts, v, actor, dims in self.st.q("SELECT event_id, ts, version, actor, dims FROM cfg_events "
                                                 "WHERE wf=? AND event_id>? ORDER BY event_id", wf, s["last_ev"]):
            s["last_ev"] = eid
            if actor != "supervisor":
                continue
            dims = eval_list(dims)
            self.st.decide(wf, "supervisor_edit", f"v{v} changed {dims}; not reverting — frozen for 48h",
                           {"version": v, "dims": dims})
            for d in dims:
                if d in ("welcome_message", "voice", "caller_id", "schedule", "cohorts"):
                    s["frozen"][d] = self.now + FREEZE_MIN
                if d in ("welcome_message", "voice"):
                    s["exp"][d] = {"status": "new"}  # experiment contaminated: restart after the freeze
                    for r in s["records"]:
                        if r["dim"] == d and r["status"] in ("active", "planned"):
                            r["status"] = "ended_by_supervisor"
            if "prompt_version" in dims:
                s["watch"] = {"since": self.now, "prompt": cfg["prompt_version"], "prev": s["prompt"],
                              "validated": False}
        s["prompt"] = cfg["prompt_version"]

    def _frozen(self, s, dim) -> bool:
        return s["frozen"].get(dim, -1) > self.now

    # ---------------------------------------------------------------- bookkeeping: planned -> active / reverted
    def _track(self, wf, s, cfg, ver):
        def outcome(pid):
            r = self.st.q("SELECT status FROM proposals WHERE pid=?", pid)
            return r[0][0] if r else None

        for dim, e in s["exp"].items():
            if e.get("status") == "planned":
                d = dist(cfg, "default", dim)
                if len(d) > 1 and outcome(e["pid"]) == "applied":
                    e.update(status="exploring", start_ver=ver)
                    self.st.decide(wf, "experiment_started", f"{dim}: arms {d} from v{ver}", {"dist": d})
                elif outcome(e["pid"]) not in ("intent", "pending", "approved", "applied"):
                    e.clear()
                    e["status"] = "new"
        for r in s["records"]:
            if r["status"] == "planned":
                o = outcome(r["pid"])
                if o == "applied":
                    r.update(status="active", start_ver=ver)
                elif o not in ("intent", "pending", "approved"):
                    r["status"] = "dropped"
            elif r["status"] == "rolling_back":
                o = outcome(r["pid_rb"])
                if o == "applied":
                    r["status"] = "rolled_back"
                elif o not in ("intent", "pending", "approved"):
                    r["status"] = "active"  # try again
        exp_on_planned = [e for e in s["exp"].values() if e.get("status") == "promote_planned"]
        for e in exp_on_planned:
            o = outcome(e["pid"])
            if o == "applied":
                e["status"] = "done"
            elif o not in ("intent", "pending", "approved"):
                e["status"] = "exploring"

    # ---------------------------------------------------------------- 2. hallucination incidents
    def _validate(self, wf, s, prompt, n, incident=False):
        key = f"{prompt}"
        if key in s["validations"]:
            return s["validations"][key]
        cost = math.ceil(n / 100)
        if self.credits - cost < (0 if incident else CREDIT_FLOOR):
            return None
        try:
            r = self.sim.post("/validation/runs", {"workflow_id": wf, "prompt_version": prompt, "n": n})
        except SimError as e:
            self.st.decide(wf, "validation_failed", f"{prompt}: {e.code}", e.body)
            return None
        self.credits = r["credits_remaining"]
        s["validations"][key] = {"id": r["validation_run_id"], "pass_rate": r["pass_rate"], "n": n}
        self.st.decide(wf, "validation_run", f"{prompt} n={n} pass_rate={r['pass_rate']} ({cost} credits)", r)
        return s["validations"][key]

    def _min_pass(self, wf):
        return self.wfs[wf]["governance"].get("min_validation_pass_rate", 0.97)

    def _incident(self, wf, s, cfg, ver) -> bool:
        p = cfg["prompt_version"]
        inc = s["incident"]
        if inc:
            if p != inc["bad"]:
                if "verify_from" not in inc:
                    inc["verify_from"] = self.now
                    self.st.decide(wf, "incident_fix_live", f"prompt {inc['bad']} -> {p}; verifying flag rate for 3h")
                elif self.now - inc["verify_from"] >= 180:
                    conn, flags = self._flags(wf, p, inc["verify_from"])
                    ok = conn == 0 or poisson_sf(flags, conn * s["base_flag"]) > 1e-3
                    self.st.decide(wf, "incident_verified" if ok else "incident_not_fixed",
                                   f"{p}: {flags} flags / {conn} connects since fix (baseline {s['base_flag']:.4f})",
                                   {"conn": conn, "flags": flags, "minutes_to_fix": inc["verify_from"] - inc["since"]})
                    if ok and p not in s["good_prompts"]:
                        s["good_prompts"].append(p)
                    s["incident"] = None
                return False
            if not self.gov.busy(wf) and self.gov.can_request(wf, reserve=True):
                return self._propose_fix(wf, s, cfg, ver)
            return False
        # detection: (a) a supervisor shipped a new prompt -> validate it now; (b) flag rate vs baseline
        reason, evidence = None, {}
        w = s.get("watch")
        if w and w["prompt"] == p and not w["validated"]:
            v = self._validate(wf, s, p, 200, incident=True)
            if v:
                w["validated"] = True
                if v["pass_rate"] < self._min_pass(wf):
                    reason = f"supervisor prompt {p} validation pass rate {v['pass_rate']} < {self._min_pass(wf)}"
                    evidence = {"validation": v}
        if p not in s["good_prompts"] or (w and w["prompt"] == p):
            conn, flags = self._flags(wf, p, self.now - 360)
            pv = poisson_sf(flags, conn * s["base_flag"])
            if flags >= 5 and pv < 1e-4:
                reason = reason or f"hallucination flags {flags}/{conn} on {p} vs baseline {s['base_flag']:.4f} (p={pv:.1e})"
                evidence["flags"] = {"conn": conn, "flags": flags, "p_value": pv}
        if not reason:
            return False
        s["incident"] = {"bad": p, "since": self.now, "reason": reason, "prev": (w or {}).get("prev")}
        self.st.decide(wf, "incident_detected", reason, evidence)
        if self.gov.can_request(wf, reserve=True) and not self.gov.busy(wf):
            return self._propose_fix(wf, s, cfg, ver)
        return False

    def _flags(self, wf, prompt, since_min):
        r = self.st.q("SELECT COALESCE(SUM(conn),0), COALESCE(SUM(flag),0) FROM en WHERE wf=? AND prompt=? "
                      "AND day*1440+hour*60 >= ?", wf, prompt, since_min - since_min % 60)[0]
        return r[0], r[1]

    def _propose_fix(self, wf, s, cfg, ver) -> bool:
        inc = s["incident"]
        mp = self._min_pass(wf)
        # Known-good prompts first: they have real engagement history. The LLM only if none validates.
        cands = [x for x in ([inc.get("prev")] + s["good_prompts"][::-1]) if x and x != inc["bad"]]
        cands = list(dict.fromkeys(cands))
        chosen = None
        for c in cands:
            v = self._validate(wf, s, c, 300, incident=True)
            if v and v["pass_rate"] >= mp:
                chosen, ev = c, v
                break
        if not chosen and self.credits >= 3:
            try:
                r = self.sim.post("/llm/suggest-fixes", {"workflow_id": wf, "problem": inc["reason"]})
                self.credits = r["credits_remaining"]
                self.st.decide(wf, "llm_suggestions", str([x["prompt_version"] for x in r["suggestions"]]), r)
                for sug in r["suggestions"]:
                    v = self._validate(wf, s, sug["prompt_version"], 300, incident=True)
                    if v and v["pass_rate"] >= mp:
                        chosen, ev = sug["prompt_version"], v
                        break
            except SimError as e:
                self.st.decide(wf, "llm_failed", e.code, e.body)
        if not chosen:
            self.st.decide(wf, "incident_no_fix", "no candidate prompt passed validation; supervisor view alert")
            return False
        new = copy.deepcopy(cfg)
        new["prompt_version"] = chosen
        pid = self.gov.request(
            wf, "incident", cfg, ver, new, reserve=True,
            summary=f"Hallucination incident: revert prompt {inc['bad']} -> {chosen} (validated {ev['pass_rate']})",
            diff=diff(cfg, new), affected=self._active_leads(wf), impact=1.0,
            dry_run={"method": "validation run on synthetic conversations", "candidate": chosen,
                     "pass_rate": ev["pass_rate"], "n": ev["n"], "required": mp,
                     "note": "validation measures safety only; engagement of the candidate is known from history"
                     if chosen in s["good_prompts"] else "candidate has no engagement history"},
            rationale=f"{inc['reason']}. {chosen} passes validation and "
                      f"{'ran in production before without elevated flags' if chosen in s['good_prompts'] else 'was suggested by the LLM'}.",
            evidence={"validation_run_id": ev["id"]})
        return bool(pid)

    # ---------------------------------------------------------------- 3. caller IDs
    def _did_stats(self, wf, hours):
        hrs = self.st.q("SELECT day, hour FROM pk WHERE wf=? GROUP BY day, hour ORDER BY day DESC, hour DESC LIMIT ?",
                        wf, hours)
        if not hrs:
            return {}
        d0, h0 = hrs[-1]
        rows = self.st.q("SELECT did, SUM(dials), SUM(conn) FROM pk WHERE wf=? AND day*24+hour >= ? GROUP BY did",
                         wf, d0 * 24 + h0)
        return {d: (n, c) for d, n, c in rows}

    def _bad(self, s) -> set:
        """Flagged caller IDs. Permanent for the run: re-probing a flagged number costs pickup and
        customer-facing spam risk for little information. (ponytail: no re-admission; add a slow
        re-probe if numbers get un-flagged by carriers in production.)"""
        return set(s["bad_dids"])

    def _flag_dids(self, wf, s, cur, stats, min_n, window):
        """Mark caller IDs whose pickup is far below comparable ones (same randomised traffic, same hours).
        Only compared against DIDs with a similar share, so an overloaded DID isn't judged against fresh probes."""
        newly = []
        for d, (n, c) in stats.items():
            if d in self._bad(s) or n < min_n or d not in cur:
                continue
            peers = [k for k in stats if k != d and k in cur and 0.5 <= cur[k] / cur[d] <= 2]
            others = peers or [k for k in stats if k != d]
            rn, rc = sum(stats[k][0] for k in others), sum(stats[k][1] for k in others)
            if rn < min_n:
                continue
            ratio = (c / n) / max(rc / rn, 1e-9)
            z = z_two_prop(c, n, rc, rn)
            if ratio < (DID_BAD_RATIO if peers else 0.5) and z < DID_BAD_Z:
                newly.append(d)
                s["bad_dids"][d] = self.now
                self.st.decide(wf, "did_degraded", f"{d} pickup {c}/{n} = {c / n:.3f} vs {'peers' if peers else 'others'} "
                               f"{rc / rn:.3f} (ratio {ratio:.2f}, z={z:.1f}) over last {window} — rotating out",
                               {"did": d, "n": n, "c": c, "rest_n": rn, "rest_c": rc, "peers": others})
        return newly

    def _did_target(self, wf, s, cfg):
        opts = self.wfs[wf]["options"]["caller_id"]
        auto = "caller_id" in self.wfs[wf]["governance"]["auto_apply"].get("allowed_dims", []) and \
            self.wfs[wf]["governance"]["auto_apply"].get("enabled")
        bad = self._bad(s)
        long = self._did_stats(wf, 24)
        def ratio(d):
            n, c = long.get(d, (0, 0))
            rn = sum(v[0] for k, v in long.items() if k != d)
            rc = sum(v[1] for k, v in long.items() if k != d)
            return (c / n) / (rc / rn) if n and rn and rc else 0.0
        # healthy = enough traffic AND pickup holding up against the rest; otherwise keep probing small
        healthy = [d for d in opts if d not in bad and long.get(d, (0, 0))[0] >= 300 and ratio(d) >= 0.85]
        unknown = [d for d in opts if d not in bad and d not in healthy]
        if not healthy:  # never empty: fall back to whatever is configured now
            healthy = [d for d in dist(cfg, "default", "caller_id") if d not in bad] or opts[:1]
            unknown = [d for d in unknown if d not in healthy]
        probe = DID_PROBE[bool(auto)]
        t = {d: probe for d in unknown}
        rest = 1 - probe * len(unknown)
        for d in healthy:
            t[d] = rest / len(healthy)
        return {d: round(w, 4) for d, w in t.items() if w > 0}, auto

    def _caller_ids(self, wf, s, cfg, ver) -> bool:
        lo, hi = self.wfs[wf]["dialling_window"]
        if not lo <= (self.now % 1440) // 60 <= hi:
            return False
        cur = dist(cfg, "default", "caller_id")
        newly_bad = self._flag_dids(wf, s, cur, self._did_stats(wf, 4), 150, "4h") + \
            self._flag_dids(wf, s, cur, self._did_stats(wf, 24), 300, "24 dialling hours")
        target, auto = self._did_target(wf, s, cfg)
        urgent = bool(newly_bad) or any(d in cur for d in self._bad(s))
        daily = (self.now % 1440) // 60 == lo and not self._frozen(s, "caller_id")
        if tv(cur, target) < 0.02 or not (urgent or daily):
            return False
        if auto:
            step = min(1.0, self.wfs[wf]["governance"]["auto_apply"]["max_shift"] * 0.999 / tv(cur, target))
            nd = {d: cur.get(d, 0) + step * (target.get(d, 0) - cur.get(d, 0)) for d in set(cur) | set(target)}
            nd = {d: round(w, 4) for d, w in nd.items() if w > 1e-4}
            if not urgent and self.gov.busy(wf):
                return False  # don't make a pending approval stale for a non-urgent rebalance
            new = copy.deepcopy(cfg)
            new["allocations"]["default"]["caller_id"] = nd
            stats = self._did_stats(wf, 24)
            return self.gov.auto(wf, "did", cfg, ver, new,
                                 rationale=("rotate out degraded caller IDs " + str(sorted(self._bad(s))) if urgent else
                                            "spread dial load evenly over healthy caller IDs; probe unknown ones at "
                                            f"{DID_PROBE[True]:.0%}") + f"; target {target}",
                                 evidence={"pickup_24h": {d: {"dials": n, "conn": c} for d, (n, c) in stats.items()}})
        if urgent and not self.gov.busy(wf) and self.gov.can_request(wf, reserve=True):
            new = copy.deepcopy(cfg)
            new["allocations"]["default"]["caller_id"] = target
            return bool(self._request(wf, "did_incident", cfg, ver, new, [f"rotate out degraded caller IDs {sorted(self._bad(s))}"],
                                      reserve=True, impact=0.8))
        return False

    # ---------------------------------------------------------------- 4. decay / rollback
    def _segs(self, cids):
        ages = [CID.index(c) for c in cids]
        return [a * 3 + t for a in ages for t in range(3)]

    def _arms(self, wf, dim, since_ver, segs, day_from=None):
        col = "wm" if dim == "welcome_message" else "voice"
        if not segs:
            return {}
        sql = (f"SELECT {col}, SUM(conn), SUM(eng) FROM en WHERE wf=? AND ver>=? AND seg IN ({','.join(map(str, segs))})"
               + (" AND day>=?" if day_from is not None else "") + f" GROUP BY {col}")
        args = (wf, since_ver) + ((day_from,) if day_from is not None else ())
        return {o: (n, e) for o, n, e in self.st.q(sql, *args)}

    def _decay(self, wf, s, cfg, ver) -> bool:
        if (self.now % 60) or not self.gov.can_request(wf, reserve=True) or self.gov.busy(wf):
            return False
        for r in s["records"]:
            if r["status"] != "active" or self._frozen(s, r["dim"]):
                continue
            a = self._arms(wf, r["dim"], r["start_ver"], self._segs(r["cids"]), day_from=self.now // 1440 - 1)
            (nc, ec), (nh, eh) = a.get(r["champ"], (0, 0)), a.get(r["hold"], (0, 0))
            if nh < 100 or nc < 100:
                continue
            z = z_two_prop(ec, nc, eh, nh)
            d_now = ec / nc - eh / nh
            se_now = math.sqrt(max(ec / nc * (1 - ec / nc), 1e-6) / nc + max(eh / nh * (1 - eh / nh), 1e-6) / nh)
            z_change = (d_now - r["d"]) / math.sqrt(se_now ** 2 + r["se"] ** 2)
            if z < -2.0 or (d_now < 0 and z_change < -3.0):
                new = copy.deepcopy(cfg)
                for c in r["cids"]:
                    new["allocations"].setdefault(c, {})[r["dim"]] = {r["hold"]: 1}
                note = (f"{r['dim']}={r['champ']} stopped winning in {r['cids']}: last 2 days "
                        f"{ec}/{nc} vs holdout {r['hold']} {eh}/{nh} (z={z:.1f}; lift at promotion {r['d']:+.3f}, "
                        f"now {d_now:+.3f}, change z={z_change:.1f}) — roll back to {r['hold']}")
                self.st.decide(wf, "decay_detected", note, {"record": r, "now": [ec, nc, eh, nh]})
                pid = self._request(wf, "rollback", cfg, ver, new, [note], reserve=True,
                                    impact=min(1.0, abs(d_now) / max(eh / nh, 1e-6) / 0.1))
                if pid:
                    r.update(status="rolling_back", pid_rb=pid)
                    return True
        return False

    # ---------------------------------------------------------------- 5. daily batch
    def _schedule(self, wf):
        lo, hi = self.wfs[wf]["dialling_window"]
        rows = self.st.q("SELECT seg/3, hour, SUM(dials), SUM(conn) FROM pk WHERE wf=? AND first=1 AND seg!=255 "
                         "GROUP BY seg/3, hour", wf)
        by = {}
        for a, h, n, c in rows:
            by.setdefault(a, {})[h] = (n, c)
        table, model = {"default": {str(h): 1.0 for h in range(lo, hi)}}, {}
        for a, cid in enumerate(CID):
            cells = by.get(a, {})
            N = sum(n for n, _ in cells.values())
            C = sum(c for _, c in cells.values())
            if N < 500:
                continue
            m = C / N
            t = {}
            for h in range(lo, hi):
                n, c = cells.get(h, (0, 0))
                r = (c + 50 * m) / (n + 50)  # shrink thin hours to the cohort mean
                t[str(h)] = round(min(max((r / m) ** 2, 0.1), 5.0), 2)
                model[(a, h)] = (r, n)
            table[cid] = t
        return table, model

    def _sched_gain(self, wf, table, model):
        """Optimistic estimate: each hour's capacity re-split across cohorts in proportion to weight."""
        lo, hi = self.wfs[wf]["dialling_window"]
        old = new = tot = 0.0
        for h in range(lo, hi):
            cells = [(model[(a, h)], table.get(CID[a], {}).get(str(h), 1.0)) for a in range(4) if (a, h) in model]
            cap = sum(n for (r, n), w in cells)
            wsum = sum(n * w for (r, n), w in cells)
            if not cap or not wsum:
                continue
            old += sum(n * r for (r, n), w in cells)
            new += sum(cap * n * w / wsum * r for (r, n), w in cells)
            tot += cap
        return (new / old - 1) if old else 0.0

    def _active_leads(self, wf, cids=None):
        L = len(self.wfs[wf]["cadence"])
        d = self.now // 1440
        leads = self.st.q("SELECT COALESCE(SUM(leads),0) FROM leads_day WHERE wf=? AND day>? AND day<=?", wf, d - L, d)[0][0]
        eng = self.st.q("SELECT COALESCE(SUM(eng),0) FROM en WHERE wf=? AND day>? AND day<=?", wf, d - L, d)[0][0]
        active = max(leads - eng, 0)
        if cids:
            segs = ",".join(map(str, self._segs(cids)))
            part = self.st.q(f"SELECT COALESCE(SUM(dials),0) FROM pk WHERE wf=? AND day>=? AND seg IN ({segs})", wf, d - 2)[0][0]
            allp = self.st.q("SELECT COALESCE(SUM(dials),0) FROM pk WHERE wf=? AND day>=?", wf, d - 2)[0][0]
            active = int(active * part / allp) if allp else active
        return int(active)

    def _request(self, wf, kind, cfg, ver, new, notes, *, reserve=False, impact=None, dry=None, evidence=None):
        dims = changed_dims(cfg, new)
        summary = f"{kind}: " + "; ".join(n.split(" — ")[0][:90] for n in notes)
        if impact is None:
            impact = 0.3
        return self.gov.request(wf, kind, cfg, ver, new, summary=summary[:300], diff=diff(cfg, new),
                                affected=self._active_leads(wf), dry_run=dry or {"method": "see rationale", "dims": dims},
                                impact=impact, rationale=" | ".join(notes), evidence=evidence, reserve=reserve)

    def _batch(self, wf, s, cfg, ver) -> bool:
        if self.gov.busy(wf) or not self.gov.can_request(wf):
            return False
        if self.st.q("SELECT 1 FROM proposals WHERE wf=? AND kind='batch' AND created>=? AND status IN "
                     "('pending','approved','applied')", wf, self.now - self.now % 1440):
            return False  # one batch per workflow per day
        pol = self.wfs[wf]["governance"]["auto_apply"]
        can_auto = lambda d: pol.get("enabled") and d in pol.get("allowed_dims", [])
        new = copy.deepcopy(cfg)
        notes, dry, gains = [], {}, {}
        ours = [c["id"] for c in cfg["cohorts"]] == CID
        foreign = bool(cfg["cohorts"]) and not ours
        if not ours and not foreign and not self._frozen(s, "cohorts"):
            new["cohorts"] = copy.deepcopy(COHORTS)
            notes.append("define age-band cohorts (shared by schedule, experiments and reporting)")
        if not foreign and not self._frozen(s, "schedule"):
            table, model = self._schedule(wf)
            old = cfg["schedule"]["hour_weights"]
            if len(table) > 1 and (not ours or _sched_moved(old, table)):
                g = self._sched_gain(wf, table, model)
                if ours and can_auto("schedule"):
                    nn = copy.deepcopy(cfg)
                    nn["schedule"]["hour_weights"] = table
                    self.gov.auto(wf, "schedule", cfg, ver, nn, f"refresh call-time weights from first-attempt "
                                  f"pickup by age x hour (est. pickup {g:+.1%})", {"weights": table})
                    return True
                new["schedule"]["hour_weights"] = table
                gains["pickup_rate"] = g
                dry["schedule"] = {"method": "first-attempt pickup by age band x hour from all observed calls "
                                             "(history + live), shrunk to cohort mean",
                                   "est_pickup_change": round(g, 4), "weights": table}
                notes.append(f"call-time weights by age band (est. pickup {g:+.1%}, optimistic)")
        if not can_auto("caller_id") and not self._frozen(s, "caller_id"):
            target, _ = self._did_target(wf, s, cfg)
            if tv(dist(cfg, "default", "caller_id"), target) >= 0.02:
                new["allocations"]["default"]["caller_id"] = target
                dry["caller_id"] = {"pickup_24h": self._did_stats(wf, 24), "target": target}
                notes.append(f"spread caller-ID load {target}")
        if ours or "cohorts" in changed_dims(cfg, new):
            self._experiments(wf, s, cfg, new, notes, dry, gains)
        if not changed_dims(cfg, new):
            return False
        prim = self.wfs[wf]["objective"]["primary"]
        impact = min(1.0, abs(gains.get(prim, 0.0)) / 0.10) if gains else 0.2
        # Record the plan under its deterministic proposal id *before* sending, so a crash in between
        # leaves a plan whose proposal never existed -> _track reverts it.
        expected = f"{wf}:batch:{self.now}"
        for e in s["exp"].values():
            if e.get("status") in ("planned", "promote_planned") and e.get("pid") is None:
                e["pid"] = expected
        for r in s["records"]:
            if r["status"] == "planned" and r.get("pid") is None:
                r["pid"] = expected
        self.st.put(f"wf:{wf}", s)
        pid = self._request(wf, "batch", cfg, ver, new, notes, impact=impact, dry=dry)
        if not pid:  # nothing was sent: undo the plan
            for e in s["exp"].values():
                if e.get("status") == "planned":
                    e.clear()
                    e["status"] = "new"
                elif e.get("status") == "promote_planned":
                    e["status"] = "exploring"
            s["records"] = [r for r in s["records"] if r["status"] != "planned"]
        return bool(pid)

    def _experiments(self, wf, s, cfg, new, notes, dry, gains):
        for dim in ("welcome_message", "voice"):
            opts = self.wfs[wf]["options"][dim]
            if len(opts) < 2 or self._frozen(s, dim):
                continue
            e = s["exp"].setdefault(dim, {"status": "new"})
            if e["status"] == "new":
                cur = dist(cfg, "default", dim)
                inc = max(cur, key=cur.get)
                alt = min(EXPLORE_ALT, 0.45 / (len(opts) - 1))
                d = {o: (1 - alt * (len(opts) - 1) if o == inc else alt) for o in opts}
                new["allocations"]["default"][dim] = d
                e.update(status="planned", inc=inc, pid=None, retired=[])
                notes.append(f"start {dim} test: {inc} keeps {d[inc]:.0%}, each challenger {alt:.0%}")
            elif e["status"] == "exploring":
                self._read_experiment(wf, s, e, dim, cfg, new, notes, dry, gains)

    def _read_experiment(self, wf, s, e, dim, cfg, new, notes, dry, gains):
        inc = e["inc"]
        taken = {c for r in s["records"] if r["dim"] == dim and r["status"] in ("planned", "active", "rolling_back")
                 for c in r["cids"]}
        pool = [c for c in CID if c not in taken]
        arms = self._arms(wf, dim, e["start_ver"], self._segs(pool))
        ni, xi = arms.get(inc, (0, 0))
        d = dict(dist(new, "default", dim))
        read = {}
        winners = []
        for o in list(d):
            if o == inc or o in e["retired"]:
                continue
            n, x = arms.get(o, (0, 0))
            z = z_two_prop(x, n, xi, ni)
            lift = (x / n) / (xi / ni) - 1 if n and ni and xi else 0.0
            read[o] = {"n": n, "eng": x, "z": round(z, 2), "lift": round(lift, 4)}
            if n >= MIN_N_DROP and z < Z_DROP:
                d[o] = 0
                e["retired"].append(o)
                notes.append(f"drop {dim}={o}: {x}/{n} vs {inc} {xi}/{ni} (lift {lift:+.1%}, z={z:.1f})")
            elif n >= MIN_N_WIN and z > Z_WIN and lift >= MIN_LIFT:
                winners.append((lift, o, z, n, x))
        dry[f"{dim}_readout"] = {"since_version": e["start_ver"], "incumbent": {"arm": inc, "n": ni, "eng": xi},
                                 "challengers": read, "cohorts_pooled": pool}
        if winners:
            lift, o, z, n, x = max(winners)
            cids, veto = [], []
            for c in pool:
                a = self._arms(wf, dim, e["start_ver"], self._segs([c]))
                (nc, xc), (nh, xh) = a.get(o, (0, 0)), a.get(inc, (0, 0))
                (veto if nc >= 80 and z_two_prop(xc, nc, xh, nh) < -1.65 else cids).append(c)
            self._promote(s, dim, o, inc, cids, x, n, xi, ni, new, notes)
            for c in veto:
                new["allocations"].setdefault(c, {})[dim] = {inc: 1}
                notes.append(f"keep {dim}={inc} for {c}: {o} looks worse there")
            new["allocations"]["default"][dim] = {inc: 1}
            e["status"] = "promote_planned"
            e["pid"] = None
            gains["engagement_rate"] = gains.get("engagement_rate", 0) + lift * (1 - HOLDOUT) * len(cids) / 4
            return
        # no pooled winner: a cohort-only winner needs stronger evidence
        for c in pool:
            a = self._arms(wf, dim, e["start_ver"], self._segs([c]))
            nh, xh = a.get(inc, (0, 0))
            for o in d:
                if o == inc or d[o] == 0:
                    continue
                nc, xc = a.get(o, (0, 0))
                if nc >= MIN_N_COH and z_two_prop(xc, nc, xh, nh) > Z_COH and xh and (xc / nc) / (xh / nh) - 1 >= MIN_LIFT:
                    self._promote(s, dim, o, inc, [c], xc, nc, xh, nh, new, notes)
                    gains["engagement_rate"] = gains.get("engagement_rate", 0) + ((xc / nc) / (xh / nh) - 1) * 0.8 / 4
                    break
        if all(d.get(o, 0) == 0 for o in d if o != inc):
            d = {inc: 1}
            e["status"] = "done"
            notes.append(f"{dim} test closed: no challenger beat {inc}")
        new["allocations"]["default"][dim] = {o: w for o, w in d.items() if w > 0}

    def _promote(self, s, dim, o, inc, cids, x, n, xi, ni, new, notes):
        for c in cids:
            new["allocations"].setdefault(c, {})[dim] = {o: 1 - HOLDOUT, inc: HOLDOUT}
        pc, ph = x / n, xi / ni
        se = math.sqrt(pc * (1 - pc) / n + ph * (1 - ph) / ni)
        s["records"].append({"dim": dim, "champ": o, "hold": inc, "cids": cids, "d": pc - ph, "se": se,
                             "status": "planned", "pid": None, "start_ver": None, "at": self.now})
        notes.append(f"promote {dim}={o} for {cids}: {x}/{n} vs {inc} {xi}/{ni} (lift {pc / ph - 1:+.1%}); "
                     f"{inc} kept as {HOLDOUT:.0%} holdout to catch decay")


def _sched_moved(old: dict, new: dict) -> bool:
    for k, t in new.items():
        o = old.get(k, {})
        for h, w in t.items():
            if abs(math.log(max(w, 1e-3) / max(o.get(h, 0), 1e-3))) > 0.35:
                return True
    return False


def eval_list(s: str) -> list:
    import json
    return json.loads(s)
