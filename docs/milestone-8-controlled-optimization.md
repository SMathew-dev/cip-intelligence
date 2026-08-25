# Milestone 8 — L6 Controlled Optimization Intelligence

## Purpose

L6 converts trustworthy evidence from L0–L5 into **controlled-validation candidates**. It does not create new validated recipes, does not approve process changes, and has no control path to a PLC/HMI.

The initial implemented optimization family is **final-rinse tail reduction**: identify cases where the plant's approved endpoint was achieved and held well before the observed end of the rinse, where comparable compliant historical cycles support a shorter behavior envelope, where QA outcome history is adequate, and where unresolved diagnostics do not make optimization inappropriate.

## Hard boundary

A nominal time target never replaces an endpoint requirement.

For an endpoint-controlled rinse:

> The plant-approved endpoint/hold condition remains authoritative. A controlled trial must not terminate the rinse merely because the nominal time target has been reached if the endpoint has not been satisfied.

CIP Intelligence cannot write the candidate into the HMI, PLC, recipe manager, or sequence logic.

## Candidate eligibility gates

The v0.1 L6 engine requires, at minimum:

- L2 assessment = `COMPLIANT`
- high-confidence explicit cycle reconstruction
- demonstrably achieved validated final-rinse endpoint
- sufficient observed tail time after endpoint hold
- sufficient asset + recipe-revision historical behavior reference
- sufficient historical QA/verification evidence for hygiene-sensitive changes
- historical verification performance above the configured plant optimization policy
- no high-severity unresolved diagnostic hypothesis when configured to block
- no confirmed unresolved condition when configured to block
- no instrumentation-quality condition that invalidates the optimization evidence
- a nontrivial opportunity versus the existing behavior reference

A plant may make these rules stricter. Product defaults are governance guardrails for development and are not regulatory or dairy-industry validation criteria.

## Trial-envelope calculation

For the initial final-rinse candidate, the proposed *nominal review target* is intentionally conservative. It is constrained by:

1. the historical upper quartile of final-rinse duration,
2. the observed validated endpoint-hold time plus a configurable guard band, and
3. a cap on the fractional reduction proposed in any single trial.

The largest of these constraints is used and rounded conservatively. This produces a reviewable experiment target, not a process instruction.

## Outcome evidence

L6 summarizes historical post-CIP verification coverage for comparable cycles. It records:

- comparable cycles
- cycles with verification
- PASS / FAIL / borderline or inconclusive counts
- verification coverage
- decisive historical pass rate

A QA result remains an outcome observation. It does not cause L6 to claim microbiological cleanliness from process sensors.

## Controlled validation workflow

The intended workflow is:

`candidate → engineering review → QA review → approved protocol → controlled trial cycles → L2 + QA + diagnostics + resource results → evidence assessment → human change-control decision`

The engine's strongest successful trial assessment is:

`EVIDENCE_SUPPORTS_HUMAN_REVIEW`

It deliberately does **not** emit `AUTO_APPROVED`, `NEW_RECIPE`, or any equivalent state.

Each controlled trial assessment can require:

- engineering approval reference
- QA approval reference
- validation protocol reference
- minimum number of trial cycles
- L2 compliance on every trial cycle
- acceptable QA verification outcomes
- no unresolved diagnostic condition
- measured resource savings where available

A verification failure or process deviation produces `REJECT_OR_INVESTIGATE` under the current development policy.

## Simulator checkpoint

With the bundled simulator-only HTST recipe and arbitrary demo economics:

- normal final rinse: no defensible reduction candidate
- excessive-rinse scenario: eligible controlled-validation candidate
- low-flow process deviation: blocked
- missing QA history: blocked
- unresolved high-severity diagnostic hypothesis: blocked

The excessive-rinse demo currently reconstructs about:

- current final rinse: 960 s
- validated endpoint hold achieved: ~500 s after rinse start
- remaining observed tail: ~450 s
- nominal controlled-trial review target: ~580 s
- potential tail reduction candidate: ~380 s

These values are simulator outputs only and are not recommendations for any real dairy plant.

## Reliability / governance rules

- compliance before optimization
- evidence before savings claims
- historical median/quantiles are references, not validated minima
- explicit QA/engineering approvals are external human decisions
- failed or missing evidence blocks adoption logic
- positive trial data never causes automatic recipe acceptance
- negative trial outcomes are retained
- all candidate/trial/decision artifacts are append-only or immutable
- plant-specific policy is versionable and auditable

## API additions

- `POST /v1/optimization/candidates`
- `GET /v1/optimization/candidates`
- `POST /v1/optimization/decisions`
- `POST /v1/optimization/trials/assess`
- `GET /v1/demo/optimization/{scenario}`

## Current limitation

The first automated discovery family is intentionally narrow: final-rinse tail-time reduction. Chemical exposure, caustic/acid duration, temperature, production-context-specific cleaning, and other optimization families remain future candidates and should only be implemented with equally explicit validation boundaries.
