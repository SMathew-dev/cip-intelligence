# Real-Plant Pilot Plan

## Pilot objective
Determine whether CIP Intelligence produces trustworthy, actionable findings on one dairy CIP circuit **without controlling the plant**.

## Preferred first dataset
- 3–12 months historian data for one circuit;
- explicit CIP sequence/step tag if available;
- return temperature, conductivity, and flow;
- pressure if available;
- approved CIP recipe/validation limits and revision history;
- ATP/verification results if available;
- production-run context if available;
- maintenance events for known abnormalities;
- dedicated water/utility meters where savings are to be quantified.

## Phase 1 — Offline onboarding
1. Copy/export data; do not connect to controls.
2. Map tags and units with engineering review.
3. Run L0 data-quality checks.
4. Compare reconstructed cycles with historian/sequence records.
5. Freeze the approved plant configuration used for analysis.

## Phase 2 — Retrospective blind evaluation
1. Build baselines only from earlier eligible cycles.
2. Score later historical cycles.
3. Hide known maintenance/QA outcomes during first-pass diagnosis where feasible.
4. Compare findings with plant records after scoring.
5. Record confirmed and disproven hypotheses.

## Phase 3 — Value review
Quantify only opportunities supported by dedicated evidence. Return-flow circulation must not be counted as water use. Required outputs include false alarms, useful findings, missed events, investigation time, and defendable resource economics.

## Phase 4 — Shadow mode
If retrospective performance is acceptable, run continuously read-only. Plant operations remain unchanged. No optimization candidate changes a recipe without normal plant engineering/QA validation and change control.

## Go/no-go criteria
The plant and project team should agree on thresholds before the prospective phase. Candidate measures include reconstruction agreement, L2 agreement, alert burden, diagnostic precision, data availability, and demonstrable investigation/resource value.
