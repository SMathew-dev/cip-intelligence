# Milestone 4 / L3 — Behavioral Intelligence

## Purpose

L2 answers a deterministic question: **did trustworthy evidence establish execution of the plant-approved validated CIP requirements?**

L3 answers a different question: **does this cycle behave like historically normal, compliant cycles for this exact asset and recipe revision?**

L3 cannot override L2. A statistically normal cycle can still fail a validated requirement, and a statistically unusual cycle can still be L2-compliant.

## Baseline identity

A baseline is bound to:

- one asset
- one logical CIP recipe
- one recipe revision
- one immutable baseline name/revision
- one behavioral-engine version and policy
- an explicit list of historical cycle lineage

CIP Intelligence does not silently mix recipe revisions or equipment. Product/production context is intentionally not modeled until L4, so L3 also exposes that limitation rather than claiming product-independent causality.

## Training eligibility

A historical cycle is eligible only when:

1. L2 assessment is `COMPLIANT`;
2. cycle reconstruction is complete;
3. reconstruction meets the configured confidence threshold;
4. explicit phase evidence is used when the baseline policy requires it;
5. asset and recipe revision match the baseline being built.

`PROCESS_DEVIATION` and `DATA_REVIEW_REQUIRED` cycles never train the normal baseline.

## Baseline poisoning guard

Even compliant cycles can be abnormal. Before freezing a baseline, L3 performs a **gross robust-outlier screen** over scalar cycle features and sustained time-series profile deviations. Cycles that are extreme across multiple features (or extraordinarily extreme in one feature) are excluded and retained in the baseline artifact as excluded evidence.

This is deliberately a gross screen, not recursive trimming. The system must retain genuine normal plant variability instead of repeatedly deleting anything inconvenient until the distribution looks perfect.

The baseline will refuse to build if screening leaves fewer than the configured minimum number of cycles.

## Robust statistics

Scalar features are summarized using robust statistics:

- median
- first and third quartiles
- median absolute deviation (MAD)
- IQR-derived scale
- engineering scale floor
- observed historical min/max

Robust deviation is calculated relative to the largest of the robust scale estimates and a small engineering-unit floor. The floors prevent nearly invariant simulator/history data from turning tiny numerical noise into absurd anomaly claims. These floors are **not CIP process limits** and cannot replace plant validation requirements.

The default development baseline requires at least 20 eligible cycles. Baseline maturity is reported as:

- `DEVELOPING`: 20–49 cycles
- `ESTABLISHED`: 50–199 cycles
- `MATURE`: 200+ cycles

These labels describe evidence depth, not a probability that an anomaly diagnosis is correct.

## Scalar fingerprint

For the cycle and each supported phase, L3 derives features such as:

- duration
- median temperature, flow, conductivity, and pressure
- robust p10/p90 tails for those signals

This allows a cycle to be flagged when, for example, caustic return flow remains above the validated L2 minimum but is materially below that asset's normal behavior.

## Time-series fingerprint

Averages can hide process shape changes. L3 therefore time-normalizes each phase and divides it into configurable relative-progress bins. It builds historical profiles for temperature, flow, conductivity, and pressure.

A profile alert requires a **sustained run of adjacent abnormal bins** rather than one isolated bin. This reduces sensitivity to one-point spikes and allows detection of patterns such as:

- unusually high flow early in caustic followed by unusually low flow later;
- a changed conductivity-decay shape;
- a slower thermal profile despite a compliant final exposure;
- a changed pressure/flow relationship across a substantial portion of a phase.

Profile deviations are `INFERRED`, not measured failures.

## Outputs

Per cycle, L3 returns one of:

- `NORMAL`
- `UNUSUAL`
- `HIGHLY_UNUSUAL`
- `NOT_EVALUABLE`

The output exposes:

- L2 assessment
- baseline identity and maturity
- scalar deviations
- direction (`HIGH` / `LOW`)
- robust deviation magnitude
- empirical position within the historical baseline
- sustained profile deviations and affected bins
- baseline lineage hash
- explicit statement that L3 cannot override L2

CIP Intelligence intentionally does **not** present the robust z-score as a probability of failure.

## Reliability guardrails

- L2 `DATA_REVIEW_REQUIRED` blocks L3 claims.
- A cycle cannot be scored against a baseline that contains that same cycle; self-comparison is withheld to prevent leakage.
- Historical scoring is withheld when the selected baseline includes observations from the same or a later time period, preventing look-ahead bias.
- Baselines are immutable and never silently retrain themselves.
- New recipe revisions require new/reviewed baselines.
- Equipment modifications or process-context changes may invalidate the usefulness of an old baseline; automatic maintenance/context handling belongs to later intelligence levels.
- No L3 anomaly is microbiological proof, a confirmed equipment diagnosis, or permission to alter a validated recipe.

## Simulator cases

The deterministic development simulator now includes two L3-specific cases:

- `compliant_low_flow`: caustic return flow remains above the demo validated minimum but is substantially below the historical equipment fingerprint.
- `profile_shift`: caustic flow is high during the first half and low during the second half while remaining above the demo validated minimum, allowing time-series fingerprinting to catch a shape change.

The existing `excessive_rinse` case remains L2-compliant and is strongly abnormal versus a normal-duration baseline.

## API

- `POST /v1/behavior/baselines` — build and freeze an asset/recipe-specific historical baseline from existing ingestions.
- `GET /v1/behavior/baselines` — list saved baselines.
- `POST /v1/behavior/ingestions/{ingestion_id}?baseline_name=...&baseline_revision=...` — evaluate an ingestion against a frozen baseline.
- `GET /v1/demo/behavior/{scenario}` — deterministic in-memory L3 demonstration.

## Current boundary

L3 knows **how this equipment normally cleans under this recipe**. It does not yet know what product ran beforehand, how long production ran, predicted fouling load, ATP/micro results, maintenance confirmations, or operator findings. Those are deliberately reserved for L4/L5 so statistical behavior is not misrepresented as causal diagnosis.
