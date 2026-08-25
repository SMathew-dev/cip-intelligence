# CIP Intelligence V1 — Release Notes

**Release:** Portfolio V1 / M11  
**Boundary:** simulated/read-only engineering prototype and pilot candidate; not a certified sanitation-release or autonomous control system.

## Included
- M1–M10 data, intelligence, UI, and production-hardening capabilities.
- M11 facility-scale seeded synthetic validation campaign and machine-readable results.
- **124/124** application regression tests passing.
- Portfolio case study, validation methodology, and real-plant pilot plan.

## M11 validation snapshot
- **4** independent synthetic HTST circuits.
- **2,240** synthetic cycles exercised (**240 baseline + 2,000 evaluation**).
- **2,000/2,000** expected L2 classifications in the seeded known-answer campaign.
- L3 nominal false-alarm rate: **6/720 (0.83%)** in the fixture.
- L3 compliant behavioral-fault detection: **640/640** across excessive-rinse, compliant-low-flow, and profile-shift scenarios.
- Frozen-sensor cycles withheld from L3 interpretation: **160/160**.

Do not generalize these simulator metrics to real plants. They demonstrate reproducible known-answer software behavior, not field accuracy or microbiological efficacy.

## Release fixes discovered during M11
- self-healing local operational/audit store after runtime-volume recreation;
- corrected validation throughput accounting;
- deterministic endpoint hold for nominal known-answer simulator scenarios;
- in-memory simulator path verified against CSV fixture output.
