"""Config helpers (mirroring the documented governance rules) and the small statistics we need."""
from __future__ import annotations

import copy
import math

AGENT_DIMS = ["welcome_message", "voice", "caller_id"]
CHANGE_DIMS = ["cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule"]


# ------------------------------------------------------------------ config
def norm(cfg: dict) -> dict:
    """Normalise like the server does: weights sum to 1, zero weights dropped, hours as strings."""
    out = copy.deepcopy(cfg)
    out.setdefault("cohorts", [])
    out.setdefault("schedule", {})
    out["schedule"].setdefault("hour_weights", {})
    out["schedule"].setdefault("lead_preferred_hour", {})
    for key, dims in out["allocations"].items():
        for dim, w in list(dims.items()):
            s = float(sum(w.values()))
            dims[dim] = {o: float(x) / s for o, x in w.items() if x > 0}
    out["schedule"]["hour_weights"] = {k: {str(int(h)): float(x) for h, x in t.items()}
                                       for k, t in out["schedule"]["hour_weights"].items()}
    return out


def dist(cfg: dict, cohort: str, dim: str) -> dict:
    return (cfg["allocations"].get(cohort) or {}).get(dim) or cfg["allocations"]["default"][dim]


def _close(a: dict, b: dict) -> bool:
    return set(a) == set(b) and all(abs(a[k] - b[k]) < 1e-9 for k in a)


def changed_dims(old: dict, new: dict) -> list[str]:
    old, new = norm(old), norm(new)
    ch = set()
    if old["cohorts"] != new["cohorts"]:
        ch.add("cohorts")
    keys = set(old["allocations"]) | set(new["allocations"])
    for d in AGENT_DIMS:
        if any(not _close(dist(old, k, d), dist(new, k, d)) for k in keys):
            ch.add(d)
    if old["prompt_version"] != new["prompt_version"]:
        ch.add("prompt_version")
    if old["schedule"] != new["schedule"]:
        ch.add("schedule")
    return [d for d in CHANGE_DIMS if d in ch]


def tv(a: dict, b: dict) -> float:
    return 0.5 * sum(abs(a.get(o, 0) - b.get(o, 0)) for o in set(a) | set(b))


def max_shift(old: dict, new: dict) -> float:
    old, new = norm(old), norm(new)
    keys = {"default"} | {c["id"] for c in old["cohorts"]} | {c["id"] for c in new["cohorts"]}
    return max(tv(dist(old, k, d), dist(new, k, d)) for k in keys for d in AGENT_DIMS)


def auto_ok(policy: dict, old: dict, new: dict) -> tuple[bool, list[str]]:
    """Local copy of the auto-apply rule, so we never burn a logged 403."""
    dims = changed_dims(old, new)
    p = policy["governance"]["auto_apply"]
    probs = []
    if not p.get("enabled"):
        probs.append("auto-apply disabled")
    if [d for d in dims if d not in p.get("allowed_dims", [])]:
        probs.append(f"dims not allowed: {dims}")
    if max_shift(old, new) > p.get("max_shift", 0) + 1e-9:
        probs.append("shift too large")
    if not dims:
        probs.append("no change")
    return not probs, probs


def diff(old: dict, new: dict) -> dict:
    """Flat path -> {old, new} diff a supervisor can read."""
    old, new = norm(old), norm(new)
    out = {}
    if old["cohorts"] != new["cohorts"]:
        out["cohorts"] = {"old": [c["id"] for c in old["cohorts"]], "new": [c["id"] for c in new["cohorts"]]}
    for k in sorted(set(old["allocations"]) | set(new["allocations"])):
        for d in AGENT_DIMS:
            a, b = (old["allocations"].get(k) or {}).get(d), (new["allocations"].get(k) or {}).get(d)
            if a != b:
                out[f"allocations.{k}.{d}"] = {"old": a, "new": b}
    if old["prompt_version"] != new["prompt_version"]:
        out["prompt_version"] = {"old": old["prompt_version"], "new": new["prompt_version"]}
    if old["schedule"] != new["schedule"]:
        out["schedule.hour_weights"] = {"old": old["schedule"]["hour_weights"], "new": new["schedule"]["hour_weights"]}
    return out


# ------------------------------------------------------------------ stats
def z_two_prop(x1: float, n1: float, x2: float, n2: float) -> float:
    """z for p1 - p2 (pooled variance). 0 when either arm is empty."""
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p), 1e-9) * (1 / n1 + 1 / n2))
    return (x1 / n1 - x2 / n2) / se


def poisson_sf(k: int, mu: float) -> float:
    """P(X >= k), X ~ Poisson(mu)."""
    if k <= 0:
        return 1.0
    term = math.exp(-mu)
    cdf = term
    for i in range(1, k):
        term *= mu / i
        cdf += term
    return max(0.0, 1.0 - cdf)


if __name__ == "__main__":
    a = {"allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1}, "caller_id": {"D1": .5, "D2": .5}}},
         "prompt_version": "P1"}
    b = copy.deepcopy(a)
    b["allocations"]["default"]["caller_id"] = {"D1": .4, "D2": .4, "D3": .2}
    assert changed_dims(a, b) == ["caller_id"] and abs(max_shift(a, b) - 0.2) < 1e-9
    assert z_two_prop(60, 100, 40, 100) > 2.8 and abs(z_two_prop(5, 10, 5, 10)) < 1e-9
    assert abs(poisson_sf(1, 2.0) - (1 - math.exp(-2))) < 1e-12 and poisson_sf(20, 3) < 1e-8
    print("util ok")
