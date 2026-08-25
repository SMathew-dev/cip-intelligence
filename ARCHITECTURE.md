# CIP Intelligence — Architecture v1

## 1. System boundary

CIP Intelligence is separate from the HMI/SCADA control surface.

```text
Field sensors / instruments
          |
          v
      PLC / DCS  ----> HMI / SCADA
          |
          +--------> Historian
                         |
                         v
                CIP Intelligence Edge
                         |
                         v
                Ingestion + semantic map
                         |
                         v
                  Immutable raw layer
                         |
                         v
                  Normalized event layer
                         |
                         v
                  Intelligence engines
                         |
                         v
                    Web application
```

The Edge connector is read-only by default and has no command authority over valves, pumps, heaters, recipes, or chemical dosing.

## 2. Source adapters

Supported progressively:

1. CSV/XLSX upload
2. Watched network folder **(implemented)**
3. Historian/database/API import **(adapter boundary defined; connectors pending)**
4. OPC UA read-only connector **(adapter boundary defined; live connector pending)**
5. MES / LIMS / CMMS / ERP integrations

Every source adapter maps source-specific tags/fields into the CIP semantic model.

## 2.1 Automated acquisition contract

All automated sources are configured as read-only and feed the same ingestion/validation pipeline used by manual files. The first implemented adapter watches an approved export directory and reads only settled files. Durable jobs preserve discovery, attempt, error, retry, source lineage, and resulting ingestion IDs.

Adapter interfaces expose discovery/read capabilities only; no plant command/write method exists in the acquisition abstraction. Live historian, SQL/API, and OPC UA integrations must implement that same boundary rather than bypassing it.

Idempotency is based on both raw source SHA-256 and a normalization-context fingerprint. Identical raw bytes are re-normalized when approved mappings/calibration/timezone/source identity differ.

## 3. Universal semantic model

Examples:

```text
TT_420_RET     -> cip.return.temperature
FIT_214        -> cip.return.flow
AIT_104        -> cip.return.conductivity
PT_207         -> cip.return.pressure
SEQ.CIP_STEP   -> cip.sequence.phase
P204.RUN       -> cip.supply_pump.state
V340.OPEN      -> cip.return_valve.state
```

Mappings retain the original source name and unit so analyses are traceable. Automatic mappings are suggestions only; plant mappings must be explicitly approved before they can serve as analytical truth. Multiple physical source tags may map to the same semantic concept so redundant instrumentation can be reconciled rather than overwritten.

## 4. Data layers

### Raw
Immutable copy of uploaded/ingested source data plus source metadata and checksum. Repeated ingestion of identical source bytes is idempotent so automated folder/API adapters cannot silently duplicate a dataset.

### Normalized
Canonical timestamps, units, equipment identity, semantic tags, quality flags.

### Events
Production run, CIP start/end, CIP phase changes, operator interventions, QA samples, maintenance events, validation changes.

### Analytics
Reconstructed cycles, compliance calculations, baselines, findings, evidence, confidence, economics.

## 5. Intelligence modules

### L0 — Data Integrity Engine
Detects missing values, duplicates, timestamp gaps, unit problems, impossible values, frozen sensors, spikes, drift evidence, and contradictory signals.

### L1 — Process Reconstruction Engine
Builds asset-specific CIP cycles and ordered phases from sequence tags when available and from sensor/state patterns when necessary. Explicit plant sequence evidence is preferred. Signal-only reconstruction is marked `INFERRED`, uses lower confidence, and may return UNKNOWN when conductivity/process evidence is insufficient. Reconstruction artifacts are immutable and versioned by source dataset + engine version + configuration.

## 5.1 Reconstruction evidence contract

- plant sequence/step tags are canonicalized but retained as source evidence
- short A-B-A phase-label glitches may be repaired conservatively
- large data gaps and clear sequence resets split cycles
- sensor-only inference uses a forward-only state machine so process order is part of the evidence
- materially disagreeing redundant instruments are withheld rather than blindly averaged
- each phase stores source method, confidence, duration, sample count, and signal metrics
- deterministic cycle IDs support lineage across downstream analyses
- reconstruction never upgrades inferred phases into measured facts

### L2 — Compliance Engine
Compares actual execution to the plant's approved recipe/validation envelope. Deterministic; ML is not allowed to override this layer.

L2 uses immutable recipe revisions with effective timestamps and approval references. It supports phase-duration, continuous-limit, qualified-exposure, concurrent-exposure, and sustained-endpoint requirements. Concurrent exposure evaluates required conditions at the same timestamps rather than comparing unrelated phase averages.

The engine distinguishes three outcomes for each requirement: `PASS`, `FAIL`, and `NOT_EVALUABLE`. Missing/unreliable evidence that could change the outcome produces `NOT_EVALUABLE`; only trustworthy measured evidence can establish a process deviation. Official compliance defaults to explicit plant phase/sequence evidence and is withheld when phases are only inferred. Analysis artifacts hash normalized source data, reconstruction, recipe revision, and engine version for reproducibility.

### L3 — Behavioral Engine
Builds immutable **asset + recipe-revision-specific** historical baselines from eligible L2-compliant cycles. Process-deviation and data-review cycles are excluded from normal training. A gross robust-outlier screen over scalar and sustained profile behavior reduces baseline poisoning without recursively trimming normal plant variability.

L3 uses two complementary fingerprints:

1. **robust scalar behavior** — cycle/phase duration plus median and robust p10/p90 temperature, flow, conductivity, and pressure features;
2. **time-normalized phase profiles** — each phase is divided into relative-progress bins so sustained changes in process shape can be detected even when phase averages remain near normal.

Distributions use median, MAD, IQR-derived scale, and small engineering scale floors. Robust deviation magnitude is an anomaly measure, **not a failure probability** and never replaces plant validation limits. Profile findings require adjacent abnormal bins rather than a single spike.

Per-cycle L3 outcomes are `NORMAL`, `UNUSUAL`, `HIGHLY_UNUSUAL`, or `NOT_EVALUABLE`. L2 `DATA_REVIEW_REQUIRED` blocks behavioral claims, and a cycle cannot be compared against a baseline that contains itself or against a baseline containing future observations relative to that historical cycle. Baselines never silently retrain; new evidence or recipe revisions create reviewed baseline revisions. L3 is explicitly non-causal until production/maintenance/QA context is added in L4/L5.


### Cross-cutting — Resource & Economics Intelligence
Dedicated utility/resource measurements are integrated separately from process-loop hydraulics. `cip.return.flow` and `cip.supply.flow` are never interpreted as fresh-water consumption. The first resource contract supports fresh-water flow, wastewater flow, electrical power, thermal power, and chemical mass-flow signals.

A resource quantity is only promoted to an official measured total when trustworthy meter coverage satisfies policy and no long unobserved intervals are integrated across. Cost profiles are plant-configured, versioned, immutable assumptions; CIP Intelligence ships no universal water/energy/chemical tariffs.

Asset + recipe-revision historical resource references are built from eligible L2-compliant cycles. Excess-versus-median findings are **optimization candidates**, not proof that the historical median is safe or optimal. Production-capacity economics apply only to excess CIP time versus the selected reference; normal required cleaning time is never mislabeled as recoverable downtime. Annualization requires an explicit plant cycle-frequency assumption.

These economics support L6 opportunity prioritization but cannot authorize recipe changes.

### L4 — Production Context Engine
Reconstructs the contiguous uncleaned production campaign immediately preceding each CIP and links product/run context to cleaning behavior. L4 preserves product codes/families, run duration, processed volume where evidenced, composition, thermal context, shutdowns/holds, pre-CIP idle time, product changes, and supplied pressure-drop/heat-transfer indicators. Multiple contiguous runs may belong to one campaign; large inter-run gaps start a new campaign.

L4 deliberately does **not** generate a universal soil-load score. Pressure-drop and normalized heat-transfer changes are retained as fouling-associated process indicators, not direct measurements of residual soil. Missing production evidence remains missing.

Immutable asset + recipe-revision context baselines are trained only from L2-compliant, explicitly reconstructed cycles with usable production context. For a new CIP, transparent robust-distance matching selects sufficiently similar historical production contexts (product-family constrained by default), then compares current cleaning behavior with that matched cohort. Outcomes are `CONTEXTUALLY_TYPICAL`, `CONTEXTUALLY_UNUSUAL`, `INSUFFICIENT_COMPARABLES`, or `NOT_EVALUABLE`. Similarity distance is not a probability and contextual consistency does not prove causation. Self-comparison and look-ahead bias are blocked.

### L5 — Diagnostic & Outcome Engine
Joins post-CIP QA/verification results, maintenance confirmations, operator observations, L2 compliance, L3 plant-specific behavior, L4 production context, and historical resolved cases. It explicitly separates **detection → diagnosis → confirmation**.

A QA failure is treated as a measured outcome, never as an automatic root cause. A compliant CIP followed by failed verification opens a cleanability/coverage/soil/verification investigation rather than claiming that bulk process data proved the equipment clean or identified the failure mechanism.

Initial hydraulic diagnoses require joint plant-specific evidence: low flow + high pressure can support a restriction hypothesis; low flow + low pressure can support a pump/supply hypothesis. Low flow by itself remains a detection. Suspicious instrumentation blocks dependent diagnosis. Physical/CMMS evidence can promote a condition to `CONFIRMED`.

Resolved diagnostic cases preserve both confirmations and negative findings. Historical support may strengthen confidence only after configured minimum evidence and empirical precision thresholds. L5 returns an evidence graph so the UI can show which observations support, constrain, trigger, or confirm each conclusion.

### L6 — Controlled Optimization Engine
Converts trustworthy L0–L5 evidence into reviewable controlled-validation candidates. The first implemented discovery family targets excess final-rinse tail time only when L2 is compliant, reconstruction is high-confidence and explicit, the validated endpoint/hold is demonstrably achieved, historical asset+recipe behavior is sufficient, QA/verification outcome coverage meets plant policy, and unresolved diagnostic evidence does not block optimization.

A nominal trial time never replaces the approved endpoint condition. The endpoint remains authoritative during every trial. L6 cannot write to the PLC/HMI, change a recipe, approve a trial, or accept a new process automatically.

The governance flow is `candidate → engineering approval → QA approval → validation protocol → controlled trial cycles → L2/QA/diagnostic/resource assessment → human change-control decision`. The strongest automated post-trial output is `EVIDENCE_SUPPORTS_HUMAN_REVIEW`, never automatic recipe adoption. Human decisions and negative trial outcomes are retained as lineage evidence.

### Explanation layer
Generative language may summarize verified findings but is never the source of pass/fail calculations or raw numeric evidence.

## 6. Reliability contract

Every output is classified as one of:

- **MEASURED** — directly observed from a trusted source
- **DERIVED** — deterministically calculated from measured/configured values
- **INFERRED** — model/statistical/diagnostic conclusion
- **UNKNOWN** — insufficient evidence

Every finding stores:

- evidence used
- evidence quality
- data coverage
- algorithm/model version
- recipe/validation revision
- alternative explanations where relevant
- confidence when probabilistic
- timestamp and source lineage

A failed or low-confidence prerequisite automatically downgrades or blocks dependent conclusions.

## 7. Initial deployment architecture

- Frontend: dependency-free browser SPA for the M9 checkpoint, served by FastAPI; a React/TypeScript production migration remains optional if it improves M10 maintainability without changing API/evidence contracts
- API + analytics: Python / FastAPI
- Operational database: PostgreSQL
- Time-series extension: TimescaleDB when needed
- Raw file/object layer: S3-compatible storage (MinIO for local development)
- Edge agent: lightweight Python service initially
- Analytics: NumPy/Pandas/Polars + scikit-learn initially; specialized models only when evidence warrants them
- Packaging/deployment: Docker

The application is deliberately vendor-agnostic; adapters surround a stable internal semantic model.

## 7A. M9 product interface

The M9 plant interface is a read-only presentation layer over the stable analytics APIs. It exposes five primary workspaces: Plant Overview, Cycle Explorer, Investigations, Controlled Optimization, and Data Health.

The UI deliberately preserves the evidence hierarchy rather than combining everything into one score. L2 compliance, L3 behavioral normality, reconstruction/data confidence, L5 diagnosis/confirmation, and L6 controlled-validation eligibility are shown independently. This permits states such as `COMPLIANT + HIGHLY_UNUSUAL` without implying contradiction.

The demo UI is served at `/app/` from the same FastAPI process and uses simulator-only presentation endpoints. M10 will replace demo overview/list fixtures with durable database-backed application queries while retaining the UI contract. The control boundary (`READ ONLY`, no PLC/HMI write path) and simulator-data label are permanent visual elements in this checkpoint.

## 8. Non-negotiable security/plant rules

- read-only plant connectivity by default
- least-privilege service accounts
- no PLC/HMI control path in V1/V2
- immutable raw-data lineage
- configurable on-premise deployment for sensitive customers
- explicit time-zone handling; normalized timestamps stored in UTC
- original engineering units retained; canonical units stored separately
- no model recommendation can silently change a validated CIP recipe

## 9. V1 scope freeze

V1 must be excellent at L0–L6, with L6 remaining strictly controlled-validation support:

- file ingestion
- semantic mapping
- data quality validation
- cycle and phase reconstruction
- recipe/version model
- deterministic compliance
- behavioral baseline/anomaly analysis
- resource/time economics
- production campaign linking and context-aware comparison
- QA/maintenance/operator evidence linking and initial explainable diagnostic library
- evidence explorer
- confidence/data-coverage reporting
- controlled optimization candidate discovery
- engineering/QA approval lineage and controlled-trial assessment

L6 remains a human-governed recommendation/validation layer and never becomes an automatic plant-control path.

## M11 release/validation boundary

Portfolio V1 is frozen after a seeded synthetic validation campaign and full regression gate. Machine-readable M11 results live under `validation/`. Synthetic ground truth is used to verify software behavior; it is not evidence of external accuracy in a dairy plant. Real-plant validation remains a separate offline/shadow-mode phase and does not change the read-only control boundary.
