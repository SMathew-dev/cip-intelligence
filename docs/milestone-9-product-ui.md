# Milestone 9 — Product UI

Milestone 9 turns the L0–L6 engineering stack into a usable read-only plant application without weakening the evidence hierarchy.

## Product UI rule

The interface must never collapse distinct evidence classes into a single opaque score. Deterministic compliance, historical behavior, data confidence, diagnosis, and controlled optimization remain visibly separate.

## Implemented screens

### Plant Overview

- 24-hour CIP activity summary
- compliant/deviation/data-review counts
- behavioral alerts
- measured utility-water summary
- asset-by-asset L2/L3/data-confidence table
- severity-ordered attention queue
- recent-cycle table
- permanent read-only control-boundary indicator

### Cycle Explorer

- scenario/cycle selection for the deterministic demo
- process time-series chart
- temperature, return flow, conductivity, and pressure metric switching
- reconstructed CIP phase band
- L2 compliance assessment
- L3 behavioral assessment
- L0/L1 evidence confidence
- validated requirement findings
- phase evidence source/confidence table

The cycle view deliberately shows `COMPLIANT + UNUSUAL` as a valid state. L3 never overwrites L2.

### Investigations

- evidence graph presentation
- detection → hypothesis → confirmation separation
- maintenance-confirmed condition display
- QA verification-failure investigation boundary
- instrumentation/data-quality blocks on diagnosis

### Controlled Optimization

- eligible optimization candidate display
- validated endpoint timing
- conservative nominal controlled-trial review envelope
- historical/QA/diagnostic eligibility gates
- plant economics scenario
- explicit engineering + QA + controlled-validation workflow
- permanent statement that the endpoint remains authoritative and there is no PLC/HMI write path

### Data Health

- overall evidence confidence
- trusted/warning/blocked signal counts
- physical tag → semantic meaning mapping visibility
- coverage/freshness
- sensor-quality issue visibility

## Presentation API

M9 adds simulator-only presentation endpoints:

- `GET /v1/demo/ui/overview`
- `GET /v1/demo/ui/data-health`
- `GET /v1/demo/ui/timeseries/{scenario}`

These endpoints are demo fixtures only. M10 replaces overview/list queries with durable PostgreSQL-backed application services while keeping the UI contract stable.

## Frontend implementation

The M9 frontend is a dependency-free browser SPA served by FastAPI from `services/api/app/static`.

Why this choice for the checkpoint:

- one command runs both API and UI;
- no Node build tool is required to evaluate the repository;
- no external CDN/assets are required;
- the UI is still separated from analytics logic and talks only through HTTP API contracts;
- M10 can introduce a framework build pipeline later if it materially improves maintainability without changing the evidence model.

## Reliability boundaries visible in UI

- `SIMULATED DATA` is always labeled in the demo.
- `Observe only / No PLC-HMI write path` is permanently visible.
- process compliance and behavioral anomaly status are separate.
- `NOT_EVALUABLE` remains a first-class result.
- measured resource values never imply an optimization is approved.
- inferred root causes remain visibly distinct from maintenance-confirmed conditions.
- controlled optimization is presented as a validation candidate, never an automatic recipe change.

## UI tests

M9 adds tests for:

- root redirect to the product UI;
- static product shell delivery;
- demo overview read-only/simulator boundaries;
- data-health visibility;
- time-series + phase preservation;
- invalid scenario rejection.

Total repository test status at this checkpoint: **117 passing tests**.

## Browser validation

The interface was rendered in a headless Chromium environment with mocked deterministic API fixtures. Overview, Cycle Explorer, Investigations, Controlled Optimization, and Data Health completed without page-level JavaScript errors.

Screenshots:

- `docs/screenshots/plant-overview.png`
- `docs/screenshots/cycle-explorer.png`

## Run

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

- Product UI: `http://127.0.0.1:8000/app/`
- API documentation: `http://127.0.0.1:8000/docs`
