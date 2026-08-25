# Milestone 1A — Universal Plant-Data Ingestion & Mapping

Status: **working checkpoint**  
API version: **0.2.0**

## Goal

CIP Intelligence must accept imperfect plant exports without silently inventing engineering meaning. Milestone 1A establishes the first production-oriented ingestion boundary:

1. preserve the source bytes unchanged;
2. fingerprint the upload with SHA-256;
3. inspect delimiter, encoding, headers, and likely timestamp field;
4. propose conservative semantic mappings;
5. require an explicitly saved mapping before normalization;
6. require explicit engineering units for numeric process measurements;
7. normalize timestamps to UTC and engineering units to canonical units;
8. retain source column, source unit, source row, asset, and quality code for lineage;
9. surface data-quality problems before downstream CIP reasoning;
10. make repeated ingestion of identical source bytes idempotent.

## Reliability rules

### Mapping suggestions are not approved mappings

`FIT_214` is deliberately treated as ambiguous because the tag name alone does not prove whether it is supply flow, return flow, product flow, or another flow measurement.

A more descriptive source column such as `CIP Return Flow [gpm]` can generate a high-confidence suggestion for `cip.return.flow`, but CIP Intelligence still does not use that suggestion as plant truth until a mapping profile is saved.

### Engineering units are explicit

A numeric process mapping that has a canonical engineering unit must declare its source unit. The ingestion service will not silently assume that a temperature is °C, a flow is L/min, or a pressure is bar.

Initial conversions include:

- °F ↔ °C
- US gpm ↔ L/min
- µS/cm ↔ mS/cm
- psi ↔ bar
- kPa ↔ bar

### Time is explicit

Source timestamps with explicit UTC offsets are normalized directly to UTC.

Naive local timestamps require a configured IANA plant timezone such as `America/Chicago`. Ambiguous/non-existent local times at daylight-saving transitions are rejected rather than guessed.

### Raw data remains traceable

For each successful ingestion, the local-development storage layer writes:

- exact original source bytes;
- SHA-256 checksum;
- immutable manifest;
- normalized JSONL records;
- normalized summary/data-quality report.

The raw file and manifest are made read-only in local development. Production object storage will use retention/versioning controls.

### Duplicate ingestion is idempotent

If an automated watched folder or scheduled export presents the exact same file again, the same checksum returns the prior ingestion rather than creating a duplicate analytical dataset.

### Redundant sensors are supported

The database model distinguishes a physical source tag from its semantic concept. Multiple instruments may therefore map to the same concept, such as two temperature measurements that both provide evidence about `cip.return.temperature`.

This is required for future evidence reconciliation and sensor-disagreement detection.

## API workflow

### 1. Inspect a source file

`POST /v1/ingestion/inspect`

Returns:

- source checksum;
- encoding;
- delimiter;
- timestamp candidate;
- per-column numeric coverage;
- semantic mapping candidates;
- source-unit guesses;
- preview rows.

No mapping is committed.

### 2. Save an approved mapping

`POST /v1/mappings`

Example conceptual mapping:

```json
{
  "name": "htst-legacy-historian",
  "plant": "Example Dairy",
  "source_system": "Legacy historian",
  "timezone": "America/Chicago",
  "timestamp_column": "Date/Time Local",
  "mappings": [
    {
      "source_column": "CIP Return Temp [F]",
      "concept": "cip.return.temperature",
      "source_unit": "F"
    }
  ]
}
```

### 3. Ingest with the saved mapping

`POST /v1/ingestion/{profile_name}`

The response includes an ingestion ID, checksum, deduplication state, object references, data coverage, and quality issues.

## Included demo

- `data/messy_historian_export.csv`
- `config/messy_demo_mapping.json`

The demo intentionally uses:

- semicolon delimiter;
- local US timestamps;
- °F;
- US gpm;
- µS/cm;
- psi;
- text CIP phases.

CIP Intelligence normalizes these to UTC, °C, L/min, mS/cm, and bar while retaining source lineage.

## Automated tests

Current tests cover:

1. opaque instrument IDs are not directionally guessed;
2. legacy semicolon CSV detection;
3. conservative semantic suggestions;
4. engineering-unit conversion;
5. plant-local timestamp normalization;
6. explicit-unit enforcement;
7. sampling-gap detection;
8. exact raw-byte/checksum preservation;
9. partial flatline detection;
10. DST ambiguity rejection;
11. duplicate-ingestion idempotency.

(The current test suite contains nine test functions; several assert multiple reliability properties.)

## Deliberately not yet claimed

Milestone 1A does **not** yet provide:

- XLSX ingestion;
- watched network folders;
- historian/database connectors;
- OPC UA connectivity;
- automatic LIMS/MES/CMMS integrations;
- database persistence through PostgreSQL;
- full cycle/phase reconstruction from unlabeled sensor patterns.

Those sit after this ingestion contract. The contract is intentionally designed so later adapters feed the same normalized semantic layer rather than creating separate analysis paths.
