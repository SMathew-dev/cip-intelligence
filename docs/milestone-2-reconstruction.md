# Milestone 2 — Automatic CIP Cycle & Phase Reconstruction

## Purpose

Milestone 2 converts normalized historian/plant data into auditable CIP events. The reconstruction engine is deliberately evidence-graded: explicit sequence tags are preferred, while sensor-only reconstruction is clearly labeled as inferred and is allowed to return UNKNOWN when evidence is insufficient.

## Reconstruction modes

### 1. Explicit phase reconstruction

When a trusted plant source provides `cip.sequence.phase`, the engine:

- canonicalizes plant-specific labels such as `ALKALI`, `RINSE 1`, and `FINAL WATER RINSE`
- groups timestamp-level evidence by asset
- repairs only short A-B-A label glitches, never arbitrary long phase changes
- splits cycles on large timestamp gaps or a clear final-to-pre-rinse sequence reset
- calculates phase duration and signal statistics
- records phase evidence as `EXPLICIT`

Example canonical sequence:

```text
PRE_RINSE
CAUSTIC
INTERMEDIATE_RINSE
ACID
FINAL_RINSE
```

### 2. Signal-inferred reconstruction

When no sufficiently complete step/phase tag exists, the engine can infer phases from return conductivity, temperature, flow, and sequence order.

The initial state machine is intentionally forward-only. This prevents an early dirty/warm rinse from being mislabeled as acid simply because a single measurement overlaps an acid range.

Current inference logic uses evidence such as:

- active CIP flow to establish likely cleaning windows
- elevated conductivity + elevated temperature to establish caustic introduction
- thermal collapse after caustic to establish intermediate rinse
- later elevated temperature + acid-range conductivity to establish acid
- thermal collapse after acid to establish final rinse

Every inferred phase is marked `INFERRED` and receives lower confidence than an explicit plant sequence tag.

## Reliability rules

1. **Explicit evidence wins.** If the sequence tag is sufficiently complete, signal inference does not overwrite it.
2. **Unknown is allowed.** Sensor-only phase inference currently requires at least 80% return-conductivity coverage.
3. **Duplicate timestamp-level points block reconstruction.** They can distort dwell calculations and must be resolved upstream.
4. **Redundant sensors are not blindly averaged.** Closely agreeing redundant sensors may be collapsed; materially disagreeing instruments are withheld from reconstruction until the evidence-reconciliation layer resolves them.
5. **Phase-label glitches are repaired conservatively.** Only short A-B-A interruptions are auto-repaired.
6. **Cycle IDs are deterministic.** They derive from asset + reconstructed time bounds.
7. **Reconstruction artifacts are immutable/versioned.** A new engine/config/source dataset creates a new lineage artifact.

## Output contract

Each reconstructed cycle contains:

- deterministic `cycle_id`
- asset
- start/end timestamps
- duration
- reconstruction mode (`EXPLICIT`, `INFERRED`, or future `HYBRID`)
- confidence
- completeness (`COMPLETE` or `PARTIAL`)
- ordered phases
- per-phase evidence source and confidence
- per-phase return temperature/flow/conductivity/pressure metrics
- reconstruction issues

## API

### Reconstruct a normalized ingestion

```text
POST /v1/reconstruction/ingestions/{ingestion_id}
```

The service reads the immutable normalized dataset, runs reconstruction, and stores a versioned reconstruction artifact under `runtime/reconstructions/`.

### Demo both evidence modes

```text
GET /v1/demo/reconstruct/normal?mode=explicit
GET /v1/demo/reconstruct/normal?mode=inferred
```

## Current test result

Milestones 1A–2 currently pass **28 automated regression tests**.

For the deterministic simulator's normal HTST cycle:

```text
Explicit reconstruction confidence: 0.995
Inferred reconstruction confidence: 0.859

PRE_RINSE            8 min
CAUSTIC             22 min
INTERMEDIATE_RINSE   7 min
ACID                 10 min
FINAL_RINSE           9 min
```

Both engines reconstruct the same known sequence while preserving their different evidence grades.

## What this milestone does not claim

- It does not determine microbiological cleanliness.
- It does not infer arbitrary plant-specific phases without evidence/configuration.
- It does not use ML to invent missing cycle history.
- It does not control the HMI/PLC or alter recipes.
- It does not yet compare reconstructed phases against a versioned plant validation specification; that is the next compliance milestone.
