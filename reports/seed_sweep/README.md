# Seed sweep grade summaries

`python -m sim grade --json` output for public scenarios re-run under other seeds (same truth, different
randomness), produced by `scripts/seed_sweep.sh`. File = `<tag>_<scenario>_s<seed>.json`; baselines use the same seed.

| Tag | Controller version |
|---|---|
| `old` | hourly decay check, z<−2 or decline vs promotion lift, 20% holdout (commit `f923fed`) |
| `new` | daily holdout-only check, z<−2, 2 days |
| `new2` | daily holdout-only, z<−1.65, 3 days, persistence, 30% holdout (commit `754f791`) |
| `new3` | new2 + shrunk-baseline decline test (rejected) |
| `gr` | new2 + guardrail monitor + guardrail-only asymmetric tests (asymmetric tests rejected) |
| `fin` | new2 + guardrail monitor |
| `fin2` | fin + cohort novelty guard read pooled (rejected) |
| `fin3` | fin + cohort novelty guard read per cohort (**final**) |
