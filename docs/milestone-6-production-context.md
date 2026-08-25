# Milestone 6 — L4 Production Context Intelligence

## Purpose

L4 connects a CIP event to the production campaign that preceded it so cleaning behavior can be interpreted in context. It answers a narrower and more reliable question than a generic fouling model:

> Is this cleaning behavior unusual **given what this equipment processed immediately before the CIP**?

L4 does **not** claim that production data directly measures residual soil, microbiological cleanliness, or causal mechanism.

## Production campaign model

A CIP may follow more than one production run. L4 therefore reconstructs the contiguous **uncleaned production campaign** immediately before the CIP rather than blindly attaching only the final batch.

The campaign retains:

- asset
- production run IDs
- product codes and product family
- batch references
- total production duration
- campaign span and internal idle time
- idle time between production end and CIP start
- product changes
- processed volume where measured/derivable
- product composition where available
- process temperature context
- shutdown/hold minutes
- pressure-drop change where supplied
- normalized heat-transfer-performance change where supplied

Large gaps between production runs start a new campaign. Production overlapping the CIP is treated as a data/event conflict.

## Evidence handling

Production-run records require timezone-aware start/end timestamps, stable source identity, asset identity, product identity, and source lineage. Run IDs are immutable in the local reference implementation.

Missing production metrics stay missing. Examples:

- no throughput or totalizer evidence → no fabricated production volume
- no pressure-drop measurements → no pressure-drop change
- no heat-transfer indicator → no heat-transfer decline

Pressure-drop and heat-transfer changes are explicitly labeled **fouling-associated process indicators**, not direct fouling measurements.

## Context features

L4 currently derives transparent run/campaign features such as:

- `production.total_duration_hours`
- `production.campaign_span_hours`
- `production.internal_idle_hours`
- `production.pre_cip_idle_hours`
- `production.run_count`
- `production.product_change_count`
- `production.total_volume_l`
- `production.weighted_fat_pct`
- `production.weighted_protein_pct`
- `production.weighted_total_solids_pct`
- `production.weighted_process_temperature_avg_c`
- `production.process_temperature_max_c`
- `production.shutdown_minutes`
- `production.pressure_drop_change_bar`
- `production.normalized_heat_transfer_decline_pct`

These are evidence for comparison. They are not collapsed into a fictional universal `soil_score`.

## Contextual comparison

An immutable L4 baseline is built only from historical cycles that:

1. belong to the same asset;
2. use the same recipe revision;
3. are L2 `COMPLIANT`;
4. have explicit cycle reconstruction;
5. have usable preceding production context.

For a new cycle, L4 finds historically compliant cycles with similar production conditions using a transparent robust-distance calculation. By default product family must match. A minimum number of shared context features and comparable cycles is required.

The selected comparable cohort is then used to evaluate the new CIP's robust behavior features.

Possible outcomes:

- `CONTEXTUALLY_TYPICAL`
- `CONTEXTUALLY_UNUSUAL`
- `INSUFFICIENT_COMPARABLES`
- `NOT_EVALUABLE`

Distance is a similarity measure, **not a probability**.

## Why this matters

A cycle can be unusual relative to the plant's overall L3 baseline while being normal among cycles that followed similar production conditions.

For example, the deterministic simulator includes two historical contexts:

- ~6-hour normal production campaigns followed by the normal CIP pattern;
- ~12-hour campaigns with stronger pressure-drop/heat-transfer changes followed by a somewhat longer but still L2-compliant cleaning pattern.

A later long production campaign followed by that longer CIP is `CONTEXTUALLY_TYPICAL`. The same longer CIP after a short normal campaign is `CONTEXTUALLY_UNUSUAL`.

This is association, not proof that the long run **caused** the longer cleaning requirement.

## Reliability rules

- L2 `DATA_REVIEW_REQUIRED` blocks L4 cleaning-behavior claims.
- self-comparison against an L4 training cycle is blocked.
- historical scoring against a baseline containing same-time/future observations is blocked.
- different product families are not silently treated as comparable when the policy requires a match.
- insufficient comparable history returns `INSUFFICIENT_COMPARABLES`, not a forced answer.
- production context never overrides L2 deterministic compliance.
- L4 does not infer microbiological cleanliness.

## Integration boundary

Production runs can arrive from MES, historian-derived event logic, database/API integrations, CSV pipelines, or manual fallback. The current checkpoint exposes an immutable production-run API/store and source lineage. Future industrial adapters feed the same internal run contract; operators are not expected to type routine production data in mature deployments.

## Demo

The API exposes:

- `GET /v1/demo/context/normal`
- `GET /v1/demo/context/long_run_response`
- `GET /v1/demo/context/unexpected_after_short_run`

All demo process values are simulator fixtures, not universal dairy thresholds.
