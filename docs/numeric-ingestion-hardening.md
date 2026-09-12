# Numeric ingestion hardening

## Problem

Python accepts sensor strings such as `NaN`, `inf`, and `1e999` as floating-point
values. NaN bypasses ordinary plausible-range comparisons, while infinity can
survive as an invalid numeric value in normalized output. These are not usable
process measurements. Finite input can also overflow during calibration or unit
conversion.

## Change

- Reject non-finite mapping scale factors and offsets during model validation.
- Check finiteness before calibration, after calibration, and after unit conversion.
- Withhold invalid numeric values as null, retain the original source text, and
  report `NON_FINITE_VALUE` as a high-severity issue.
- Exclude these points from good-point counts and data coverage. Reconstruction
  receives a missing measurement, not fabricated numeric evidence.
- Preserve raw source bytes unchanged.

## Verification boundary

Regression tests cover NaN/infinity strings, exponent overflow, invalid calibration,
calibration arithmetic overflow, unit-conversion overflow, strict JSON serialization,
and persistence through reconstruction alongside a valid measurement.

This is an ingestion reliability fix, not real-plant validation, proof of cleanliness,
or authorization for sanitation release. Historical artifacts are not rewritten;
previously ingested data should be reviewed before being reused for a pilot.
