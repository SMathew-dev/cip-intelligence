# V1 Validation Methodology

## Purpose
The M11 campaign is a reproducible **known-answer software validation exercise**. It tests whether CIP Intelligence behaves as designed when the ground truth is controlled by the simulator. It does not estimate real dairy-plant accuracy.

## Separation of evidence
- L2 is evaluated against explicit synthetic recipe requirements.
- L3 is trained only on earlier compliant cycles for the same synthetic asset and recipe revision.
- Evaluation cycles occur after baseline observations to avoid look-ahead leakage.
- Sensor-freeze scenarios are expected to become `DATA_REVIEW_REQUIRED` / `NOT_EVALUABLE`, not process-failure diagnoses.

## Release metrics
### L2 expected classification rate
Fraction of evaluation cycles whose deterministic L2 assessment matches the scenario's known-answer class.

### Normal L3 false-alarm rate
Fraction of nominal compliant evaluation cycles classified as `UNUSUAL` or `HIGHLY_UNUSUAL`.

### Behavioral-fault detection rate
Fraction of deliberately compliant-but-behaviorally-abnormal cycles (`excessive_rinse`, `compliant_low_flow`, `profile_shift`) classified as `UNUSUAL` or `HIGHLY_UNUSUAL`.

## Why synthetic metrics are limited
The simulator and detector share engineering assumptions. Real plants introduce tag errors, instrument drift, unusual recipes, changing products, maintenance modifications, human interventions, undocumented controls, sensor lag, and failure modes not represented here. Therefore the release metrics must never be marketed as field sensitivity, specificity, diagnostic accuracy, or sanitation efficacy.

## Real-world metrics for a pilot
A retrospective/shadow-mode plant pilot should measure at minimum:

- data coverage and rejected-data rate;
- reconstruction accuracy against known cycle/step records;
- L2 false-positive and false-negative rates against plant review;
- L3 alert rate and engineer-confirmed usefulness;
- diagnostic precision on physically resolved cases;
- confidence calibration;
- time-to-investigation reduction;
- measured water/chemical/energy/time opportunities using dedicated meters;
- percentage of recommendations rejected by engineering/QA;
- outcome of controlled validation trials.
