# Milestone 1B — Automated Plant Data Acquisition

## Goal

Move routine data input away from manual uploads without weakening the Milestone 1A reliability contract.

CIP Intelligence remains a separate, read-only system. It discovers approved plant data, reads it, routes it through the exact same mapping/normalization/data-quality pipeline as a manual upload, and creates durable acquisition-job records.

## Implemented in this checkpoint

### Watched-folder adapter

A plant can point CIP Intelligence at a historian/SCADA/MES/LIMS export folder. The adapter:

- discovers configured file patterns
- ignores hidden and temporary files
- ignores files that have not remained untouched for the configured settle period
- opens source files with read-only OS flags
- never renames, deletes, modifies, or moves plant source files
- creates a durable acquisition job before processing
- uses the saved engineering-approved mapping profile
- routes source bytes through the immutable raw + normalized ingestion pipeline

### Acquisition sources

Saved source configuration contains:

- source name
- adapter type
- mapping profile
- read-only flag (must be true)
- enabled state
- polling interval
- adapter-specific configuration

The data model rejects a source configured with `read_only=false`.

### Durable jobs

Each candidate file becomes a job with:

- discovery/source reference
- mapping profile
- status
- attempt count
- ingestion ID
- duplicate state
- persisted error on failure
- retry lineage

Failed candidates are not marked as complete, so a corrected configuration/file can be retried.

### Idempotency

There are two protections:

1. **Acquisition-state idempotency** — the same settled source object is not reprocessed on every poll.
2. **Ingestion-context idempotency** — normalized data is reused only when both the raw bytes and normalization context match.

The second rule matters because the same raw file normalized under a revised tag mapping/calibration is *not* the same analytical dataset.

### Adapter contract

All industrial adapters inherit a deliberately small read-only interface:

- `discover()`
- `read()`

There is no `write`, `command`, `set`, or `control` method.

`historian`, `database`, `api`, and `opcua` are reserved adapter types in the source model, but Milestone 1B does **not** pretend those live connectors exist yet. Invoking one returns an explicit not-implemented error.

## API

- `POST /v1/acquisition/sources`
- `GET /v1/acquisition/sources`
- `POST /v1/acquisition/sources/{source_name}/run`
- `GET /v1/acquisition/jobs`
- `POST /v1/acquisition/jobs/{job_id}/retry`

## Worker

A source can be polled outside the API process:

```bash
cd services/api
python -m app.acquisition.worker --source plant-historian-export-folder --once
```

Continuous polling:

```bash
python -m app.acquisition.worker --source plant-historian-export-folder
```

The source's configured `poll_seconds` controls the interval.

## Reliability changes made during 1B

### Normalization-context fingerprinting

Milestone 1A originally deduplicated an ingestion on raw SHA-256 alone. That is insufficient: identical source bytes can produce materially different normalized evidence if an approved mapping, unit conversion, calibration scale, timezone, or source identity changes.

Milestone 1B fingerprints both:

- raw source bytes
- normalization context

Only an exact match is treated as the same analytical ingestion.

### Redundant sensor database fix

The original relational schema keyed sensor readings by semantic concept + time + plant, which would prevent two physical instruments from both representing `cip.return.temperature` at the same timestamp.

The schema now keys readings by the physical `tag_mapping_id`, preserving redundant instrumentation for later sensor-reconciliation logic.

## Test status

Milestone 1B regression suite: **16 passing tests**.

Coverage includes:

- safe semantic suggestions
- explicit engineering units
- unit conversions
- local-to-UTC handling
- DST ambiguity rejection
- sampling-gap detection
- immutable raw-byte preservation
- partial flatline detection
- source read-only enforcement
- watched-folder settle behavior
- automated ingestion
- source-object idempotency
- normalization-context idempotency
- failed-job persistence/retry
- explicit refusal to fake an unimplemented OPC UA connection

## What is intentionally not implemented yet

- live historian connector
- SQL/database connector
- OPC UA live subscription/read connector
- MES/LIMS/CMMS APIs
- credentials/secrets vault
- production message broker
- clustered workers / HA

Those should be added behind the existing adapter boundary rather than changing the intelligence pipeline.
