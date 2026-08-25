# CIP Intelligence V1.1 — Historical Intelligence

V1.1 extends the V1 simulator-backed portfolio release with multi-cycle historical analysis designed to answer a different question from single-cycle compliance:

> Which assets are gradually becoming abnormal, resource-intensive, or investigation-worthy over time?

## Added

- deterministic 30 / 60 / 90-day synthetic CIP history across five demo assets
- asset-level attention ranking based on transparent, bounded evidence components
- flow, temperature, cycle-duration, and water-use trend calculations
- repeated process-deviation, data-review, and behavioral-alert counts
- historical water screening against each asset's stable simulator baseline
- dedicated **Historical Intelligence** product screen
- regression tests proving deterministic history, expected known-answer drift patterns, complete history windows, advisory-only scoring, and static-fixture consistency
- GitHub Actions CI running the full Python regression suite plus JavaScript syntax validation

## Known-answer simulator patterns

The public fixture intentionally contains different historical behaviors:

- **HTST-01** — stable reference behavior
- **HTST-02** — gradual hydraulic decline and increasing cycle duration
- **VAT-04** — late temperature deterioration producing process deviations
- **SILO-07** — stable reference behavior
- **UF-01** — increasing cycle duration and water use plus explicit data-quality review events

## Safety / interpretation boundary

Historical attention scores are **investigation-prioritization outputs only**. They do not overwrite deterministic L2 compliance, prove microbiological cleanliness, authorize sanitation release, change a validated CIP recipe, or write to PLC/HMI systems.

The public V1.1 history is deterministic synthetic known-answer data. Asset-specific simulator thresholds exist only to create test scenarios and are not universal CIP recommendations. Real deployments must use the plant's approved asset/recipe specifications and require offline real-plant validation before performance claims are made.

## Validation

V1.1 adds focused historical regression coverage on top of the existing V1 test suite. CI must pass before merge to `main`.
