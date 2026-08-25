# Milestone 5 — Resource & Economics Intelligence

## Purpose

Turn trustworthy CIP utility/resource evidence into auditable quantities and plant-configured economics without confusing process-loop circulation with resource consumption or manufacturing fake ROI.

## Critical engineering boundary

**CIP return/supply circulation flow is not fresh-water consumption.** A return loop can recirculate the same solution many times. Water use is only reported when CIP Intelligence has a dedicated fresh-water/makeup utility signal or, in a future extension, an explicitly labeled configured estimate. The current engine will return `NOT_EVALUABLE` rather than multiply return flow by phase duration.

## Implemented resource signals

Canonical utility/resource concepts currently include:

- `cip.utility.fresh_water.flow` → L/min
- `cip.utility.wastewater.flow` → L/min
- `cip.utility.electric.power` → kW
- `cip.utility.thermal.power` → kW
- `cip.chemical.caustic.mass_flow` → kg/min
- `cip.chemical.acid.mass_flow` → kg/min
- `cip.chemical.sanitizer.mass_flow` → kg/min

These are separate from `cip.return.flow` and `cip.supply.flow`.

## Accounting method

Trustworthy sampled rates are time-integrated into:

- fresh water: m³
- wastewater: m³
- electricity: kWh
- thermal energy: kWh
- caustic/acid/sanitizer: kg

Integration refuses long unobserved intervals. If trustworthy meter coverage falls below policy, the quantity is `NOT_EVALUABLE`; a partial observed quantity may be retained for troubleshooting but is not promoted into the official total.

Duplicate semantic utility readings at the same timestamp are withheld rather than blindly averaged until redundant-meter reconciliation is explicitly modeled.

## Cost profiles

There are **no built-in industry rates**. A plant supplies a versioned cost profile containing whichever marginal rates it wants to use. Examples include water, wastewater, electric, thermal energy, chemical, and incremental production-capacity value.

A cost profile revision is immutable. Changing a tariff/assumption requires a new revision so a historical economic result remains reproducible.

## Historical resource reference

A resource baseline is asset + recipe-revision specific and is built from eligible L2-compliant cycles. Current references use historical medians for cycle duration and sufficiently covered resource quantities.

The baseline is explicitly a **historical comparison reference, not a validated optimum**.

## Optimization candidates

When a current trustworthy quantity materially exceeds its historical median, CIP Intelligence can surface an `OPTIMIZATION_CANDIDATE` with:

- actual quantity
- historical median
- excess quantity
- configured marginal cost impact, if available

Excess CIP duration can produce a recoverable-capacity candidate only for the **excess versus reference**, never for the entire necessary CIP duration.

This protects against a common bad-ROI mistake where all cleaning time is falsely labeled lost production.

## Annualization

Annual opportunity is only calculated when the plant explicitly supplies an annual cycle-frequency assumption. The result is labeled a scenario, not guaranteed savings.

## Simulator example

The bundled simulator now emits separate development-only utility signals. For `excessive_rinse`, the current deterministic demo adds roughly 2.9 m³ of fresh water, 2.9 m³ wastewater, and 7 minutes versus the simulator's historical normal reference.

`config/example_cost_profile.json` contains arbitrary values solely to exercise the calculation path. They are **not dairy-industry benchmarks or recommended plant rates**.

Try:

`GET /v1/demo/economics/normal`

`GET /v1/demo/economics/excessive_rinse`

## API workflow for real normalized data

1. Map dedicated utility/resource signals during ingestion.
2. Run L1 reconstruction and L2 compliance.
3. Save a versioned cost profile with `POST /v1/economics/cost-profiles`.
4. Build an asset + recipe-specific historical resource reference with `POST /v1/economics/baselines`.
5. Evaluate a later ingestion with `POST /v1/economics/ingestions/{ingestion_id}`.

## Reliability limitations

- Costs are only as accurate as plant-configured marginal rates.
- Historical median is not a validated minimum or proven optimum.
- Resource excess does not prove that reducing it is safe or operationally feasible.
- A capacity opportunity does not authorize a shorter validated CIP recipe.
- Resource-reduction optimization candidates are suppressed when L2 is not `COMPLIANT`; actual measured resource cost may still be reported.
- L6 controlled validation remains responsible for proving any proposed recipe/process optimization.
