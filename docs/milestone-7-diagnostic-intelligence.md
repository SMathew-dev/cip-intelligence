# Milestone 7 — L5 Outcome & Diagnostic Intelligence

## Purpose
L5 joins post-CIP verification, maintenance findings, operator observations, L2 compliance, L3 behavior, and L4 production context without collapsing them into a black-box diagnosis.

## Reliability hierarchy
1. **MEASURED** — QA outcome or physical observation recorded by an approved source.
2. **DERIVED** — deterministic detection from trusted signals.
3. **INFERRED** — root-cause hypothesis supported by multiple evidence streams.
4. **CONFIRMED** — physical/maintenance evidence confirms the condition.
5. **UNKNOWN** — evidence cannot support a claim.

A failed ATP/micro/allergen/visual verification result is an outcome, not a root cause. A compliant CIP followed by failed verification triggers a cleanability/coverage/soil/sampling investigation; it does not prove a blocked spray device, a chemistry problem, or microbiological contamination source.

## Automatic evidence linking
Evidence is linked to a CIP by asset and configurable time windows:
- QA verification: default 12 h after CIP
- maintenance confirmation: default 168 h after CIP
- operator observations: 2 h before through 24 h after CIP

Wrong-asset and out-of-window records are not silently attached.

## Initial diagnostic signatures
### Hydraulic restriction
Requires plant-specific L3 evidence showing **low flow + high pressure**. Low flow alone produces only a hydraulic deviation detection. Alternatives remain visible until physical confirmation.

### Pump/supply performance
Requires plant-specific L3 evidence showing **low flow + low pressure**. Alternatives include suction limitation, command/speed problems, instrumentation, and configuration changes.

### Frozen flow measurement
Suspicious flow flatline blocks hydraulic root-cause inference. The system reports instrumentation/data unreliability instead.

### Compliant CIP + failed verification
The system records the failed verification and opens a cleanability/outcome investigation. It explicitly avoids treating bulk CIP compliance as proof of surface cleanliness.

## Confirmation learning
Diagnostic cases preserve both confirmed and not-confirmed outcomes. Historical empirical support can increase confidence only after a configured minimum number of resolved cases and minimum observed precision. Negative findings are retained; they are not discarded.

## Evidence graph
Every L5 result returns nodes and relationships separating detection, hypothesis, and physical confirmation. This supports an explainable "why?" view in the future UI.

## Current boundary
The initial library is intentionally small. L5 v0.1 proves the diagnostic architecture; M7 does not claim comprehensive dairy failure coverage. The failure library will expand with equipment-specific rules and real confirmed plant cases.
