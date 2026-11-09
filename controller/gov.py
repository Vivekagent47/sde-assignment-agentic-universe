"""Governance client: proposal lifecycle, rate/time budgeting, crash reconciliation.

States (proposals.status):
  intent -> pending -> approved -> applied
                    -> rejected | expired           (terminal; brain re-plans with fresh data)
         approved   -> lapsed | stale               (terminal; brain re-plans)
  intent -> unsent                                  (crash before the POST landed; brain re-plans)
  auto:  intent -> applied | stale | refused

The proposal row is written *before* the HTTP call. proposal_id is deterministic
(wf:kind:sim_minute), so a replay after a crash hits 409 duplicate and we look the
outcome up instead of acting twice.
"""
from __future__ import annotations

import json

from .api import Sim, SimError
from .store import Store, fmt_min
from .util import auto_ok, changed_dims, norm

OPEN = ("intent", "pending", "approved")


class Gov:
    def __init__(self, sim: Sim, st: Store, wfs: dict):
        self.sim, self.st, self.wfs = sim, st, wfs

    @property
    def now(self) -> int:
        return self.st.now

    # ---------------------------------------------------------------- budgeting
    def busy(self, wf: str) -> bool:
        return bool(self.st.q("SELECT 1 FROM proposals WHERE wf=? AND path='approval' AND status IN "
                              "('intent','pending','approved') LIMIT 1", wf))

    def sent_24h(self, wf: str) -> int:
        return self.st.q("SELECT COUNT(*) FROM proposals WHERE wf=? AND path='approval' AND status NOT IN ('unsent','refused') "
                         "AND created > ?", wf, self.now - 1440)[0][0]

    def can_request(self, wf: str, reserve: bool = False) -> bool:
        g = self.wfs[wf]["governance"]
        lo, hi = g["approver_hours"]
        day, mod = divmod(self.now, 1440)
        # Voluntary: nothing in the approvers' first hour (standups) unless it's an incident.
        earliest = lo * 60 if reserve else (lo + 1) * 60
        if not earliest <= mod < hi * 60:
            return False
        # Don't send something that will expire in the queue overnight.
        lat_max = g["approver_latency_minutes"][1]
        next_lo = (day + 1) * 1440 + lo * 60
        # (a decision that spills past approver hours lands at the next opening, see docs §4.1)
        if mod + lat_max >= hi * 60 and self.now + g["proposal_ttl_hours"] * 60 < next_lo:
            return False
        cap = g["max_requests_per_24h"]
        if not reserve:  # keep at least one slot for incidents/rollbacks; Kestrel asked for <= 2/day
            cap = max(1, min(2, cap - 1))
        return self.sent_24h(wf) < cap

    # ---------------------------------------------------------------- submit
    def _row(self, pid, wf, kind, path, base, reserve, body):
        self.st.db.execute("INSERT OR REPLACE INTO proposals(pid, wf, kind, path, status, base_version, created, "
                           "updated, reserve, body) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (pid, wf, kind, path, "intent", base, self.now, self.now, int(reserve), json.dumps(body)))

    def _set(self, pid, **kw):
        kw["updated"] = self.now
        cols = ", ".join(f"{k}=?" for k in kw)
        self.st.db.execute(f"UPDATE proposals SET {cols} WHERE pid=?", (*kw.values(), pid))

    def auto(self, wf: str, kind: str, cur: dict, base: int, new: dict, rationale: str, evidence: dict) -> bool:
        ok, probs = auto_ok(self.wfs[wf], cur, new)
        if not ok:
            return False
        pid = f"{wf}:{kind}:{self.now}"
        if self.st.q("SELECT 1 FROM proposals WHERE pid=?", pid):
            return False
        body = {"proposal_id": pid, "base_version": base, "proposed_config": norm(new), "rationale": rationale,
                "evidence": evidence}
        self._row(pid, wf, kind, "auto", base, False, body)
        try:
            r = self.sim.put(f"/workflows/{wf}/config", body)
        except SimError as e:
            if e.code == "duplicate_proposal_id":
                return self._reconcile_auto(pid, wf)
            self._set(pid, status="stale" if e.code == "stale_base_version" else "refused", result=json.dumps(e.body))
            self.st.decide(wf, "auto_apply_failed", f"{kind}: {e.code}", e.body, pid)
            return False
        self._set(pid, status="applied", applied_version=r["version"], result=json.dumps(r))
        self.st.decide(wf, "auto_applied", f"{kind} -> v{r['version']} dims={r['changed_dims']}",
                       {"rationale": rationale, "evidence": evidence}, pid)
        return True

    def request(self, wf: str, kind: str, cur: dict, base: int, new: dict, *, summary: str, diff: dict,
                affected: int, dry_run: dict, impact: float, rationale: str, evidence: dict | None = None,
                reserve: bool = False) -> str | None:
        dims = changed_dims(cur, new)
        # Local pre-validation: a malformed request still counts against the approver's 24h budget.
        if not dims or not summary.strip() or not diff or not dry_run or not rationale.strip():
            return None
        if "prompt_version" in dims and not (evidence or {}).get("validation_run_id"):
            return None
        pid = f"{wf}:{kind}:{self.now}"
        if self.st.q("SELECT 1 FROM proposals WHERE pid=? AND status != 'unsent'", pid):
            return None
        body = {"proposal_id": pid, "workflow_id": wf, "base_version": base, "proposed_config": norm(new),
                "change_summary": summary, "diff": diff, "affected_leads": int(max(affected, 0)),
                "dry_run": dry_run, "impact_score": round(min(max(impact, 0.0), 1.0), 3), "rationale": rationale,
                "evidence": evidence or {}}
        self._row(pid, wf, kind, "approval", base, reserve, body)
        try:
            r = self.sim.post("/approvals", body)
        except SimError as e:
            if e.code == "duplicate_proposal_id":
                self._reconcile_approval(pid, wf)
                return pid
            self._set(pid, status="unsent" if e.code == "stale_base_version" else "refused", result=json.dumps(e.body))
            self.st.decide(wf, "request_failed", f"{kind}: {e.code}", e.body, pid)
            return None
        self._set(pid, status="pending", approval_id=r["approval_id"], result=json.dumps(r))
        self.st.decide(wf, "approval_requested", f"{kind} {r['approval_id']}: {summary}",
                       {"dims": dims, "impact": body["impact_score"], "affected": body["affected_leads"],
                        "rationale": rationale, "expires_at": r.get("expires_at")}, pid)
        return pid

    # ---------------------------------------------------------------- poll / apply
    def poll(self):
        for pid, wf, aid, status in self.st.q("SELECT pid, wf, approval_id, status FROM proposals "
                                              "WHERE path='approval' AND status IN ('pending','approved')"):
            a = self.sim.get(f"/approvals/{aid}")
            s = a["status"]
            if s == "approved":
                self._apply(pid, wf, aid)
            elif s in ("rejected", "expired", "lapsed"):
                self._set(pid, status=s, result=json.dumps(a))
                self.st.decide(wf, f"approval_{s}", f"{aid} ({pid}) reasons={a.get('reasons')}", a, pid)
            elif s == "applied":  # we applied it, then crashed before recording
                self._set(pid, status="applied", result=json.dumps(a))

    def _apply(self, pid, wf, aid):
        try:
            r = self.sim.post(f"/workflows/{wf}/apply", {"approval_id": aid})
        except SimError as e:
            if e.code == "not_approved" and e.body.get("detail", {}).get("status") == "applied":
                self._set(pid, status="applied")
                return
            status = {"stale_base_version": "stale", "approval_lapsed": "lapsed"}.get(e.code, "refused")
            self._set(pid, status=status, result=json.dumps(e.body))
            self.st.decide(wf, f"apply_{status}", f"{aid}: {e.code} — will re-propose on the current version",
                           e.body, pid)
            return
        self._set(pid, status="applied", applied_version=r["version"], result=json.dumps(r))
        self.st.decide(wf, "approval_applied", f"{aid} -> v{r['version']} dims={r['changed_dims']}", r, pid)

    # ---------------------------------------------------------------- crash recovery
    def reconcile(self):
        for pid, wf, path in self.st.q("SELECT pid, wf, path FROM proposals WHERE status='intent'"):
            if path == "auto":
                self._reconcile_auto(pid, wf)
            else:
                self._reconcile_approval(pid, wf)
        self.poll()

    def _reconcile_auto(self, pid, wf) -> bool:
        for v in self.sim.get(f"/workflows/{wf}/config/history"):
            if v["proposal_id"] == pid:
                self._set(pid, status="applied", applied_version=v["version"])
                return True
        self._set(pid, status="unsent")
        return False

    def _reconcile_approval(self, pid, wf):
        for a in self.sim.get("/approvals", workflow_id=wf):
            if a["proposal_id"] == pid:
                self._set(pid, status=a["status"] if a["status"] != "applied" else "applied",
                          approval_id=a["approval_id"], result=json.dumps(a))
                self.st.decide(wf, "reconciled", f"{pid} found on server as {a['approval_id']} ({a['status']})",
                               a, pid)
                return
        self._set(pid, status="unsent")
        self.st.decide(wf, "reconciled", f"{pid} never reached the server; will re-plan", None, pid)

    def summary(self) -> str:
        rows = self.st.q("SELECT status, COUNT(*) FROM proposals GROUP BY status")
        return ", ".join(f"{s}={n}" for s, n in rows) + f" @ {fmt_min(self.now)}"
