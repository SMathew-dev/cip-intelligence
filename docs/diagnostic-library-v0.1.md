# Diagnostic Library v0.1

Each diagnostic record must separate **detection**, **diagnosis**, and **recommendation**.

## FM-HYD-001 — Possible return-side restriction

**Family:** Hydraulics

**Detection evidence**
- return flow materially below equipment/phase baseline
- upstream or return pressure / differential pressure elevated
- pump command approximately unchanged

**Corroborating evidence**
- route/valve states consistent with expected circuit
- fluid temperature/chemistry comparable to baseline
- same symptom persists across repeated phases/cycles

**Alternative explanations**
- flowmeter error
- pressure transmitter error
- incorrect valve feedback
- pump performance change
- entrained air / foaming
- changed circuit configuration

**Allowed output**
- DETECTION: `Return flow behavior is abnormal.`
- DIAGNOSIS: `Evidence is consistent with a possible hydraulic restriction.`
- NEVER: `The pipe is blocked.` unless physically confirmed and recorded.

**Confirmation sources**
- maintenance inspection
- valve/spray-device inspection
- post-repair hydraulic recovery

---

## FM-INS-001 — Frozen flow signal

**Family:** Instrumentation

**Detection evidence**
- flow value remains exactly/near-exactly constant for an implausible interval
- pump/pressure/valve state changes occur during the same interval

**Allowed output**
- `Flow measurement is unreliable during this interval; flow-dependent conclusions are downgraded.`

---

## FM-THM-001 — Validated temperature exposure not achieved

**Family:** Thermal

**Detection evidence**
- trusted temperature measurement below the plant-approved minimum during required exposure interval

**Output class:** DERIVED / deterministic

**Allowed output**
- `Validated temperature requirement was not continuously achieved.`

---

## FM-RIN-001 — Excessive final rinse candidate

**Family:** Rinsing / efficiency

**Detection evidence**
- plant-defined validated endpoint is achieved
- rinse continues substantially beyond endpoint
- trusted endpoint signal remains stable in acceptable range

**Allowed output**
- `Potential controlled optimization candidate.`

**Not allowed**
- automatic recipe shortening
- claiming sanitation equivalence without plant validation
