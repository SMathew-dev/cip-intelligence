# Milestone 3 — Validated CIP Compliance Engine (L2)

Milestone 3 turns a reconstructed CIP cycle into a deterministic, versioned assessment against a **plant-approved validation specification**.

## Safety / engineering boundary

CIP Intelligence does not ship universal dairy CIP thresholds. The included `config/example_htst_validated_recipe_v7.json` is a **simulated development recipe only**. A real deployment must import the site's approved recipe/validation envelope, engineering units, revision, effective date, and approval reference.

L2 answers: **Did the available trustworthy process evidence demonstrate execution of the approved CIP requirements?**

It does not answer: **Is the equipment microbiologically clean?** QA verification remains a separate evidence layer.

## Requirement types

The engine supports five deterministic requirement forms:

- `PHASE_DURATION` — minimum duration for a reconstructed phase.
- `CONTINUOUS_LIMIT` — a condition must remain within limit, with an explicit allowed excursion.
- `QUALIFIED_EXPOSURE` — one signal condition must be satisfied for a minimum accumulated duration.
- `CONCURRENT_EXPOSURE` — multiple conditions must be true at the **same time** for a minimum duration. This prevents a false pass where temperature is adequate at one time and flow is adequate at another.
- `ENDPOINT` — an endpoint condition must be measured and continuously sustained at the end of a phase for a configured hold time.

## Reliability rules

### Explicit phase evidence by default

Official L2 compliance is withheld when a required phase is only inferred from process signals. The default recipe policy is:

```json
"allow_inferred_phase_for_compliance": false
```

Signal-only reconstruction remains useful for analysis, but it is not silently upgraded into plant-recorded evidence.

### Missing evidence is not a process failure

If trustworthy measured data proves a requirement was missed, the requirement is `FAIL`.

If missing/unreliable data could change the outcome, the requirement is `NOT_EVALUABLE` instead. That causes `DATA_REVIEW_REQUIRED`, not a false process deviation.

### Data gaps do not count as exposure

Exposure is integrated using timestamp dwell intervals. Large intervals are capped relative to the observed sampling cadence so historian gaps cannot create fictional compliant exposure.

### Sensor flatlines

A suspicious exact flatline on a required signal blocks dependent compliance. There is one narrow context-aware exception for an `ENDPOINT`: a stable endpoint plateau can be accepted only when it follows a sufficiently long, genuinely changing signal prefix and the plateau itself satisfies the configured endpoint condition. A signal flat from the beginning of the phase remains suspicious and is withheld.

### Recipe revisions are immutable

Once a recipe revision is saved, saving different content under the same asset/name/revision is rejected. A later revision is created instead. The effective revision at the cycle timestamp is selected deterministically. If multiple different logical recipes are valid for the same asset and the cycle does not identify which recipe ran, the engine refuses to guess.

## Output statuses

Each requirement returns:

- `PASS`
- `FAIL`
- `NOT_EVALUABLE`

Cycle-level assessment:

- `COMPLIANT` — all configured requirements passed.
- `PROCESS_DEVIATION` — at least one requirement is measurably failed.
- `DATA_REVIEW_REQUIRED` — no measured failure is established, but at least one requirement cannot be proven from trustworthy evidence.

A measured failure remains a `PROCESS_DEVIATION` even if other requirements also have missing evidence; the data limitation is still preserved in the individual findings.

## Evidence record

Every requirement stores, as applicable:

- recipe name/revision/approval reference
- phase evidence source and confidence
- phase start/end/duration
- required metrics
- data coverage
- configured conditions
- qualified exposure seconds
- known violating seconds
- unknown seconds
- endpoint tail-hold duration
- sensor-flatline evidence
- engine version

The compliance artifact additionally hashes:

- normalized source data
- reconstruction artifact
- exact recipe revision(s)

Changing any of those creates a different analysis artifact.

## Demo recipe

`config/example_htst_validated_recipe_v7.json` currently demonstrates:

- pre-rinse duration
- caustic phase duration
- simultaneous caustic temperature + flow + conductivity exposure
- simultaneous acid temperature + conductivity exposure
- sustained final-rinse conductivity endpoint

Again, these values are simulation fixtures, **not plant recommendations**.

## API

Save a validated recipe revision:

```text
POST /v1/compliance/recipes
```

List loaded revisions:

```text
GET /v1/compliance/recipes
```

Evaluate a normalized + reconstructed ingestion:

```text
POST /v1/compliance/ingestions/{ingestion_id}
```

If the asset can run more than one logical recipe, specify the plant-recorded recipe name rather than allowing inference:

```text
POST /v1/compliance/ingestions/{ingestion_id}?recipe_name=HTST%20Full%20CIP
```

Built-in deterministic demo:

```text
GET /v1/demo/compliance/normal
GET /v1/demo/compliance/low_temp
GET /v1/demo/compliance/low_flow
GET /v1/demo/compliance/sensor_freeze
GET /v1/demo/compliance/excessive_rinse
GET /v1/demo/compliance/normal?mode=inferred
```

Expected high-level results:

| Scenario | Assessment |
|---|---|
| normal | COMPLIANT |
| low_temp | PROCESS_DEVIATION |
| low_flow | PROCESS_DEVIATION |
| sensor_freeze | DATA_REVIEW_REQUIRED |
| excessive_rinse | COMPLIANT |
| normal + inferred phases | DATA_REVIEW_REQUIRED |

## Regression coverage at checkpoint

The repository has 44 passing tests across ingestion, automated acquisition, data quality, reconstruction, and compliance. Milestone 3 tests include concurrent exposure, missing-data uncertainty, sustained endpoints, stable-endpoint vs frozen-sensor discrimination, recipe immutability, effective revision selection, artifact idempotency, and API behavior.
