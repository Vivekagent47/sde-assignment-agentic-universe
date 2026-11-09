"""Controller tests where a bug would hurt: governance rules, ingest atomicity, request timing.
Run: python -m pytest -q tests/test_controller.py"""
import json

import pytest

from controller.gov import Gov
from controller.store import Store
from controller.util import auto_ok, changed_dims, max_shift, poisson_sf, z_two_prop

CFG = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1},
                                                  "caller_id": {"D1": .5, "D2": .5}}},
       "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}
WF = {"governance": {"auto_apply": {"enabled": True, "allowed_dims": ["caller_id", "schedule"], "max_shift": 0.2},
                     "approver_hours": [10, 19], "approver_latency_minutes": [20, 120], "proposal_ttl_hours": 12,
                     "apply_window_hours": 2, "max_requests_per_24h": 4}}


def _with(**alloc):
    c = json.loads(json.dumps(CFG))
    c["allocations"]["default"].update(alloc)
    return c


def test_auto_apply_rules_match_policy():
    ok, _ = auto_ok(WF, CFG, _with(caller_id={"D1": .4, "D2": .4, "D3": .2}))
    assert ok
    ok, probs = auto_ok(WF, CFG, _with(caller_id={"D1": .2, "D2": .5, "D3": .3}))  # TV 0.3 > 0.2
    assert not ok and "shift too large" in probs
    ok, probs = auto_ok(WF, CFG, _with(voice={"V2": 1}))  # customer-facing dim not allowed
    assert not ok
    assert changed_dims(CFG, _with(voice={"V1": 2})) == []  # weights normalise: no change


def test_max_shift_is_total_variation():
    assert abs(max_shift(CFG, _with(caller_id={"D1": 1})) - 0.5) < 1e-9


def test_stats():
    assert z_two_prop(60, 100, 40, 100) > 2.8
    assert poisson_sf(12, 3.3) < 1e-3 < poisson_sf(5, 3.3)


def _lines(events):
    return [json.dumps(e) for e in events]


LEAD = {"type": "lead", "ts": "2027-01-05T08:00:00+05:30", "workflow_id": "w", "campaign_id": "w:c1",
        "lead_id": "w:l1", "person_id": "w:p0000007", "features": {"lang": "hi", "city_tier": "2",
                                                                    "age_band": "60+", "value_band": "mid"}}
CALL = {"type": "call", "ts": "2027-01-05T09:07:00+05:30", "workflow_id": "w", "lead_id": "w:l1",
        "person_id": "w:p0000007", "attempt_no": 1, "config_version": 1, "cohort_id": "default",
        "variant": {"welcome_message": "W1", "voice": "V1", "caller_id": "D1", "prompt_version": "P1"},
        "connected": True, "engaged": True, "qa": {"hallucination_flag": False}}


def test_ingest_joins_features_and_is_atomic(tmp_path):
    st = Store(str(tmp_path / "s.db"))
    assert st.ingest(0, _lines([LEAD, CALL])) == 2
    assert st.q("SELECT day, hour, seg, did, first, dials, conn FROM pk") == [(1, 9, 3 * 3 + 1, "D1", 1, 1, 1)]
    assert st.get("cursor") == 0

    def boom():
        yield json.dumps(CALL)
        raise ConnectionError("stream died mid-chunk")
    with pytest.raises(ConnectionError):
        st.ingest(1, boom())
    # nothing from the half-read chunk landed and the cursor did not move
    assert st.q("SELECT SUM(dials) FROM pk")[0][0] == 1 and st.get("cursor") == 0
    st2 = Store(str(tmp_path / "s.db"))  # restart: segment map survives
    assert st2.segmaps["w"][7] == 10


def test_request_timing_and_budget(tmp_path):
    st = Store(str(tmp_path / "s.db"))
    g = Gov(None, st, {"w": WF})
    st.now = 1440 + 10 * 60 + 15          # D1 10:15: approvers' first hour -> only incidents
    assert not g.can_request("w") and g.can_request("w", reserve=True)
    st.now = 1440 + 11 * 60
    assert g.can_request("w")
    st.now = 1440 + 18 * 60 + 30          # would be decided tomorrow 10:00 > 12h TTL -> expires in queue
    assert not g.can_request("w", reserve=True)
    st.now = 1440 + 12 * 60
    for i in range(2):                    # normal cap = min(2, 4-1); the rest is reserve
        st.db.execute("INSERT INTO proposals(pid, wf, path, status, created) VALUES(?,?,?,?,?)",
                      (f"p{i}", "w", "approval", "rejected", st.now - 60))
    assert not g.can_request("w") and g.can_request("w", reserve=True)
