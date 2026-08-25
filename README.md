
## V1 release status — M11 complete

CIP Intelligence is now **portfolio-complete V1**. The final seeded facility-scale validation campaign exercised **2,240 synthetic CIP cycles across four simulated HTST circuits** (240 baseline + 2,000 evaluation), and the full application regression suite passes **124/124 tests**. See `docs/milestone-11-validation-release.md` for the exact results and limitations.

**Important:** these are synthetic known-answer regression results, not real-plant accuracy claims. V1 remains read-only, does not prove microbiological cleanliness, does not approve sanitation release, and cannot modify PLC/HMI recipes or commands. The next step is an offline anonymized real-plant validation pilot.

Release documents: `RELEASE_NOTES_V1.md`, `docs/validation-methodology.md`, `docs/portfolio-case-study.md`, and `docs/real-plant-pilot-plan.md`.

# CIP Intelligence

CIP Intelligence is a read-only cleaning decision-intelligence platform for dairy and food plants.

It is designed to ingest plant data, validate data quality, reconstruct CIP cycles, verify execution against plant-approved cleaning specifications, learn normal equipment behavior, correlate production/QA/maintenance context, and surface evidence-backed diagnostic and optimization opportunities.

## Product rule

**Evidence before AI. Reliability before cleverness.**

CIP Intelligence never controls a plant in the initial product architecture. PLC/HMI systems remain responsible for control. CIP Intelligence observes approved data sources through files, historians, databases, APIs, or read-only industrial connectors.

## Intelligence ladder

- L0 — Data Trust
- L1 — CIP Reconstruction
- L2 — Validated Process Compliance
- L3 — Behavioral Intelligence
- L4 — Production Context Intelligence
- L5 — Outcome & Diagnostic Intelligence
- L6 — Controlled Optimization Intelligence
- LX — Research-only capabilities until validated

## Repository status

Architecture v1 is frozen and **Milestones 1A–9 — Universal Ingestion, Automated Acquisition, CIP Reconstruction, Validated Compliance, Behavioral Intelligence, Resource & Economics Intelligence, Production Context Intelligence, Outcome/Diagnostic Intelligence, Controlled Optimization Intelligence, and Product UI** are implemented as working checkpoints. The repository includes:

- universal CIP semantic model
- initial database schema with physical-tag identity and redundant-sensor support
- read-only integration architecture
- reliability contract
- diagnostic library seed
- deterministic simulator
- deterministic first-pass analysis engine
- conservative CSV inspection and semantic-mapping suggestions
- explicit mapping profiles with engineering units and plant timezone
- UTC/unit normalization with source lineage
- immutable raw-file/checksum storage for local development
- duplicate-ingestion idempotency
- initial ingestion data-quality checks
- automated regression tests
- read-only watched-folder acquisition
- durable acquisition sources/jobs with retry lineage
- polling worker for zero-touch export ingestion
- normalization-context fingerprinting for safe idempotency
- industrial adapter interface for future historian/database/API/OPC UA connectors
- automatic asset-specific CIP cycle reconstruction
- explicit PLC/sequence-tag phase reconstruction with canonical phase aliases
- conservative sensor-only phase inference with evidence-grade confidence
- deterministic cycle IDs and immutable/versioned reconstruction artifacts
- per-phase duration and process-signal metrics
- conservative phase-glitch repair, gap/reset cycle splitting, and UNKNOWN behavior when evidence is inadequate
- versioned plant-approved recipe/validation model with immutable revisions
- deterministic L2 compliance engine with `PASS` / `FAIL` / `NOT_EVALUABLE`
- simultaneous validated-exposure calculations across temperature, flow, chemistry, etc.
- sustained endpoint checks that reject transient threshold crossings
- missing-evidence logic that distinguishes process failure from inability to prove compliance
- context-aware sensor-flatline blocking, including safe stable-endpoint handling
- compliance lineage hashes for normalized data, reconstruction, and recipe revision
- immutable/idempotent compliance analysis artifacts
- L3 asset- and recipe-revision-specific behavioral baselines
- compliant-history eligibility gates so process deviations and data-review cycles cannot train "normal" behavior
- gross robust-outlier screening to reduce baseline poisoning
- median/MAD/IQR-based robust feature distributions with engineering scale floors
- scalar cycle fingerprints for duration, temperature, flow, conductivity, and pressure behavior
- time-normalized phase profiles that can detect sustained shape changes hidden by averages
- explicit `NORMAL` / `UNUSUAL` / `HIGHLY_UNUSUAL` / `NOT_EVALUABLE` behavioral outcomes
- baseline maturity, lineage, immutable revisions, and self-comparison leakage protection
- strict L2/L3 separation: behavioral anomalies never override deterministic validated compliance

- dedicated utility/resource semantic concepts kept separate from recirculating process flow
- meter-coverage-aware integration for fresh water, wastewater, electricity, thermal energy, and chemical mass
- `MEASURED` / `NOT_EVALUABLE` resource accounting that refuses long unobserved gaps
- immutable plant-configured cost profiles with no built-in fake industry rates
- asset + recipe-revision historical resource references from eligible L2-compliant cycles
- optimization candidates for excess resource use and excess CIP time versus historical median
- production-capacity value applied only to excess cleaning time, never to the whole necessary CIP
- annualized opportunity scenarios only when cycle frequency is explicitly configured
- L2 gate that suppresses resource-reduction recommendations on noncompliant/data-review cycles while retaining measurable resource accounting
- L4 immutable production-run evidence model with MES/historian/database/API/CSV/manual/simulator source lineage
- automatic reconstruction of the contiguous uncleaned production campaign preceding each CIP
- campaign features for production duration, volume, composition, product changes, idle time, shutdowns, pressure-drop change, and normalized heat-transfer decline where evidenced
- explicit refusal to convert those production variables into a fictional universal soil-load score
- asset + recipe-revision L4 baselines trained only from contextual L2-compliant explicit cycles
- transparent robust nearest-context matching with product-family constraints and minimum-comparable-history requirements
- `CONTEXTUALLY_TYPICAL` / `CONTEXTUALLY_UNUSUAL` / `INSUFFICIENT_COMPARABLES` / `NOT_EVALUABLE` outcomes
- L4 self-comparison and historical look-ahead leakage protection
- L5 immutable QA, maintenance, operator-observation, and resolved diagnostic-case evidence stores
- automatic asset/time-window evidence linking without silently attaching unrelated records
- explicit separation of post-CIP verification outcome from root-cause diagnosis
- hydraulic diagnostic signatures that require joint plant-specific L3 flow/pressure evidence rather than low flow alone
- instrumentation-quality gating that blocks root-cause inference when a required signal is unreliable
- physical/CMMS confirmations that remain distinct from inferred hypotheses
- historical positive **and negative** confirmation learning with minimum-evidence/precision gates before confidence is upgraded
- explainable evidence graph separating detections, hypotheses, and confirmed conditions
- L6 final-rinse tail optimization discovery gated by L2 compliance, explicit reconstruction, endpoint evidence, historical behavior, QA outcomes, and diagnostic status
- conservative controlled-trial envelopes that never replace the plant-approved endpoint condition
- explicit engineering/QA/protocol approval references for controlled-trial assessment
- trial assessment that can support human review but can never auto-accept a recipe change
- immutable optimization candidates, human decision records, and controlled-validation assessments
- industrial product UI with Plant Overview, Cycle Explorer, Investigations, Controlled Optimization, and Data Health screens
- simulator-only presentation API for overview, signal time-series, and data-health fixtures
- browser-rendered product screenshots under `docs/screenshots/`
- 124 automated regression tests passing across ingestion through L6 controlled optimization intelligence, M9 UI contracts, M10 production hardening, and M11 release safeguards

## Run the demo API

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:

- Product UI: `http://127.0.0.1:8000/app/`
- API docs: `http://127.0.0.1:8000/docs`

The M9 UI is deliberately served by the same FastAPI process so the checkpoint is runnable without a separate frontend build. See `docs/milestone-9-product-ui.md`.

### Try the ingestion demo

Use `data/messy_historian_export.csv` with `config/messy_demo_mapping.json`. The API workflow is:

1. `POST /v1/ingestion/inspect` to inspect a CSV and receive mapping suggestions.
2. `POST /v1/mappings` to explicitly save an approved mapping.
3. `POST /v1/ingestion/{profile_name}` to preserve and normalize the source data.

See `docs/milestone-1a-ingestion.md` for the ingestion reliability contract, `docs/milestone-1b-acquisition.md` for automated acquisition, `docs/milestone-2-reconstruction.md` for cycle/phase reconstruction, `docs/milestone-3-compliance.md` for deterministic validated compliance, `docs/milestone-4-behavioral-intelligence.md` for L3 historical behavior learning, and `docs/milestone-5-resource-economics.md` for resource/cost accounting, and `docs/milestone-6-production-context.md` for L4 production-context intelligence, and `docs/milestone-7-diagnostic-intelligence.md` for L5 outcome/diagnostic intelligence, and `docs/milestone-8-controlled-optimization.md` for L6 controlled optimization, and `docs/milestone-9-product-ui.md` for the plant-facing application interface.


### Try CIP reconstruction

After a normalized ingestion exists, call `POST /v1/reconstruction/ingestions/{ingestion_id}`. For the built-in deterministic simulator, compare `GET /v1/demo/reconstruct/normal?mode=explicit` with `GET /v1/demo/reconstruct/normal?mode=inferred`. The inferred result is intentionally lower-confidence even when it reconstructs the same known sequence.

### Try validated compliance

The bundled `config/example_htst_validated_recipe_v7.json` is a **simulation fixture, not a universal CIP recommendation**. Real deployments must load the plant's approved validation specification.

For the deterministic simulator, try `GET /v1/demo/compliance/normal`, `.../low_temp`, `.../low_flow`, `.../sensor_freeze`, and `.../excessive_rinse`. To use a real normalized ingestion, save a recipe with `POST /v1/compliance/recipes`, reconstruct the ingestion, then call `POST /v1/compliance/ingestions/{ingestion_id}`.


### Try behavioral intelligence

L3 requires an immutable baseline built from historical cycles for the **same asset and recipe revision**. The default policy requires at least 20 eligible L2-compliant cycles and screens gross compliant outliers before freezing the baseline. Use `POST /v1/behavior/baselines` with `config/example_behavior_baseline_request.json` as a shape reference, then evaluate a new ingestion with `POST /v1/behavior/ingestions/{ingestion_id}`.

For the built-in simulator, try `GET /v1/demo/behavior/normal`, `.../compliant_low_flow`, `.../profile_shift`, `.../excessive_rinse`, `.../low_temp`, and `.../sensor_freeze`. The important distinction is that `compliant_low_flow`, `profile_shift`, and `excessive_rinse` can remain L2-compliant while L3 reports behavior that is historically unusual.


### Try resource & economics intelligence

Resource accounting uses **dedicated utility meters/signals**; return-flow circulation is never treated as fresh-water use. Save plant-specific rates with `POST /v1/economics/cost-profiles`, build an asset + recipe-revision historical reference with `POST /v1/economics/baselines`, and evaluate later data with `POST /v1/economics/ingestions/{ingestion_id}`.

For the built-in simulator, compare `GET /v1/demo/economics/normal` with `GET /v1/demo/economics/excessive_rinse`. The bundled dollar rates are explicitly simulator-only and are not industry benchmarks.



### Try production-context intelligence

Save production-run events with `POST /v1/context/production-runs`. In mature plants those payloads are expected to come automatically from MES/historian/database/API adapters; manual entry is only a fallback. Build an immutable asset + recipe-revision context baseline with `POST /v1/context/baselines`, then evaluate a later ingestion with `POST /v1/context/ingestions/{ingestion_id}`.

For the deterministic simulator, compare `GET /v1/demo/context/long_run_response` with `GET /v1/demo/context/unexpected_after_short_run`. The first uses a longer CIP after a historically comparable long production campaign and is contextually typical; the second uses the same CIP behavior after a short normal campaign and is contextually unusual. L4 reports association, never proof of causation or cleanliness.

### Try automated watched-folder ingestion

Save a read-only source with `POST /v1/acquisition/sources`, then run it with `POST /v1/acquisition/sources/{source_name}/run`. For a standalone poller, use `python -m app.acquisition.worker --source <name>`.

## Important safety boundary

CIP Intelligence can determine whether available process evidence indicates that a plant-defined/validated CIP process was executed as specified. It must not represent process measurements alone as proof that equipment is microbiologically clean.


### Try outcome & diagnostic intelligence

Store QA results with `POST /v1/diagnostics/qa-results`, maintenance findings with `POST /v1/diagnostics/maintenance-events`, operator observations with `POST /v1/diagnostics/operator-observations`, and resolved diagnostic cases with `POST /v1/diagnostics/cases`. After reconstruction + L2 compliance (and ideally L3/L4 artifacts) exist, call `POST /v1/diagnostics/ingestions/{ingestion_id}`.

For the simulator, try `GET /v1/demo/diagnostics/verification_failure`, `.../restriction_confirmed`, `.../sensor_freeze`, and `.../normal`. The v0.1 library intentionally refuses to diagnose a hydraulic restriction from low flow alone; plant-specific L3 flow + pressure behavior or a physical confirmation is required.

### Try controlled optimization intelligence

L6 converts prior evidence into a **controlled-validation candidate**, never an automatic recipe change. Start with `GET /v1/demo/optimization/excessive_rinse` and compare it with `.../normal`, `.../low_flow`, or `.../sensor_freeze`. The excessive-rinse simulator demonstrates a final-rinse endpoint that is achieved well before the phase ends; L6 proposes a conservative nominal trial envelope while keeping the approved endpoint/hold condition authoritative.

Material candidates can be frozen with `POST /v1/optimization/candidates`. Human plant decisions are recorded separately with `POST /v1/optimization/decisions`. After approved controlled trial cycles are available, `POST /v1/optimization/trials/assess` checks L2 compliance, QA outcomes, diagnostics, and measured savings. Even a perfect trial only returns `EVIDENCE_SUPPORTS_HUMAN_REVIEW`; formal recipe adoption remains a plant engineering/QA/change-control decision. See `config/example_optimization_policy.json` and `config/example_trial_assessment_request.json`.
