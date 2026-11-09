# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.388 | +15.1% | 0.264 | +8.5% | 0.520 | +4.8% | 35% | 458 (409) |
| arav_kyc_1 | 0.490 | +36.7% | 0.277 | +28.7% | 0.606 | +1.0% | 13% | 461 (352) |
| arav_renewal_0 | 0.468 | +15.5% | 0.283 | +7.3% | 0.585 | +6.8% | 39% | 463 (408) |
| kest_cancel_2 | 0.383 | +13.7% | 0.263 | +8.6% | 0.513 | +2.8% | 30% | 462 (418) |
| kest_kyc_1 | 0.456 | +28.4% | 0.260 | +21.5% | 0.606 | +1.4% | 13% | 387 (370) |
| kest_renewal_0 | 0.471 | +17.1% | 0.285 | +7.6% | 0.584 | +7.2% | 39% | 451 (403) |
| nort_cancel_2 | 0.385 | +13.4% | 0.262 | +7.2% | 0.521 | +4.6% | 33% | 435 (368) |
| nort_kyc_1 | 0.495 | +39.0% | 0.276 | +28.4% | 0.612 | +2.8% | 21% | 431 (318) |
| nort_renewal_0 | 0.468 | +15.4% | 0.283 | +7.2% | 0.582 | +6.3% | 37% | 454 (419) |
| tide_cancel_2 | 0.385 | +12.1% | 0.261 | +6.9% | 0.521 | +3.9% | 34% | 408 (401) |
| tide_kyc_1 | 0.493 | +38.5% | 0.277 | +29.2% | 0.609 | +1.8% | 17% | 470 (376) |
| tide_renewal_0 | 0.467 | +15.3% | 0.283 | +7.1% | 0.582 | +6.6% | 40% | 452 (425) |
| ALL | 0.446 | +21.6% | 0.273 | +13.3% | 0.571 | +4.6% | 29% | 5332 (4667) |

## Changes applied by the controller

62 applied — neutral: 23, beneficial: 39

- nort_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.014 → **neutral**
- nort_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- kest_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.014 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- arav_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.014 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- tide_renewal_0 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=-0.014 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.006 → **neutral**
- arav_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- nort_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- tide_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.019 → **neutral**
- kest_renewal_0 v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.055 → **beneficial**
- nort_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- kest_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- arav_cancel_2 v3 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nort_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.022 didΔ=+0.115 → **beneficial**
- nort_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.022 didΔ=+0.123 → **beneficial**
- tide_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.022 didΔ=+0.127 → **beneficial**
- kest_renewal_0 v4 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'schedule'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v2 (controller_approved) dims=['cohorts', 'welcome_message', 'voice', 'caller_id', 'schedule'] engageΔ=+0.022 didΔ=+0.130 → **beneficial**
- nort_renewal_0 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.031 → **beneficial**
- nort_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- kest_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- arav_renewal_0 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.031 → **beneficial**
- arav_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- tide_renewal_0 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.031 → **beneficial**
- tide_cancel_2 v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- nort_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- kest_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- arav_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- tide_cancel_2 v5 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.009 → **neutral**
- arav_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.063 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.063 didΔ=+0.000 → **beneficial**
- nort_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.342 → **beneficial**
- nort_cancel_2 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.063 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.408 → **beneficial**
- arav_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.090 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v6 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.090 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.090 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.072 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.465 → **beneficial**
- kest_kyc_1 v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.299 → **beneficial**
- nort_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.017 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v6 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- kest_cancel_2 v7 (controller_approved) dims=['voice'] engageΔ=+0.047 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nort_kyc_1 v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.081 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.042 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v4 (controller_approved) dims=['voice'] engageΔ=+0.038 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.042 didΔ=+0.000 → **beneficial**
- nort_renewal_0 v8 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- kest_cancel_2 v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- tide_kyc_1 v5 (controller_approved) dims=['voice'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- arav_kyc_1 v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.041 didΔ=+0.000 → **beneficial**
- kest_kyc_1 v5 (controller_approved) dims=['voice'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v7 (controller_approved) dims=['voice'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- arav_cancel_2 v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 41,
  "decisions": {
    "approved": 40,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 39,
  "auto_applied": 23,
  "auto_apply_rejected": 0,
  "apply_conflicts": 1,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_renewal_0": 1,
    "nort_kyc_1": 1,
    "nort_cancel_2": 1,
    "kest_renewal_0": 2,
    "kest_kyc_1": 1,
    "kest_cancel_2": 2,
    "arav_renewal_0": 1,
    "arav_kyc_1": 2,
    "arav_cancel_2": 1,
    "tide_renewal_0": 2,
    "tide_kyc_1": 2,
    "tide_cancel_2": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nort_renewal_0:welcome_message=W3": {
    "3": 0.699,
    "4": 0.702
  },
  "kest_renewal_0:welcome_message=W3": {
    "3": 0.702,
    "4": 0.704
  },
  "arav_renewal_0:welcome_message=W3": {
    "3": 0.701,
    "4": 0.702
  },
  "tide_renewal_0:welcome_message=W3": {
    "3": 0.7,
    "4": 0.699
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0