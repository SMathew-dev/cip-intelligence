from __future__ import annotations
import math, statistics
from datetime import datetime, timedelta
from typing import Any

from app.intelligence.data_quality import detect_flatline
from app.reconstruction.models import SignalPoint
from .models import DiagnosisPolicy, QAResult, MaintenanceEvent, OperatorObservation

ENGINE_VERSION = "0.1.0"


def _ts(v: str | datetime) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if d.tzinfo is None: raise ValueError("L5 requires timezone-aware timestamps")
    return d


def link_evidence(cycle: dict, qa: list[QAResult], maintenance: list[MaintenanceEvent], observations: list[OperatorObservation], policy: DiagnosisPolicy) -> dict:
    start, end = _ts(cycle["start_ts"]), _ts(cycle["end_ts"])
    asset = cycle["asset"]
    linked_qa=[q for q in qa if q.asset==asset and end <= q.sample_ts <= end+timedelta(hours=policy.qa_link_hours_after_cip)]
    linked_m=[m for m in maintenance if m.asset==asset and end <= m.event_ts <= end+timedelta(hours=policy.maintenance_link_hours_after_cip)]
    linked_o=[o for o in observations if o.asset==asset and start-timedelta(hours=policy.operator_link_hours_before_cip) <= o.event_ts <= end+timedelta(hours=policy.operator_link_hours_after_cip)]
    return {
        "qa_results": [x.model_dump(mode="json") for x in sorted(linked_qa,key=lambda x:x.sample_ts)],
        "maintenance_events": [x.model_dump(mode="json") for x in sorted(linked_m,key=lambda x:x.event_ts)],
        "operator_observations": [x.model_dump(mode="json") for x in sorted(linked_o,key=lambda x:x.event_ts)],
    }


def _phase_points(points: list[SignalPoint], cycle: dict, phase_name: str) -> list[SignalPoint]:
    phases=[p for p in cycle.get("phases",[]) if p.get("phase")==phase_name]
    if len(phases)!=1:return []
    s,e=_ts(phases[0]["start_ts"]),_ts(phases[0]["end_ts"])
    return [p for p in points if p.asset==cycle["asset"] and s <= p.ts <= e]


def _hydraulic_signature(cycle: dict, points: list[SignalPoint]) -> dict[str, Any]:
    pts=_phase_points(points, cycle, "CAUSTIC")
    if not pts:return {"status":"UNAVAILABLE"}
    flows=[p.return_flow_lpm for p in pts if p.return_flow_lpm is not None]
    pressures=[p.return_pressure_bar for p in pts if p.return_pressure_bar is not None]
    if not flows or not pressures:return {"status":"UNAVAILABLE"}
    flat=detect_flatline([float(v) for v in flows])
    return {
        "status":"UNRELIABLE" if any(i.code=="FLATLINE" for i in flat) else "AVAILABLE",
        "flow_median_lpm": round(statistics.median(flows),3),
        "flow_min_lpm": round(min(flows),3),
        "pressure_median_bar": round(statistics.median(pressures),4),
        "pressure_max_bar": round(max(pressures),4),
        "flatline_flags":[i.__dict__ for i in flat],
    }


def _historical_support(code: str, cases: list[dict], policy: DiagnosisPolicy) -> dict:
    relevant=[c for c in cases if c.get("diagnosis_code")==code and c.get("confirmation_status") in {"CONFIRMED","NOT_CONFIRMED"}]
    confirmed=sum(c.get("confirmation_status")=="CONFIRMED" and (c.get("confirmed_code") in {None,code}) for c in relevant)
    negative=sum(c.get("confirmation_status")=="NOT_CONFIRMED" or (c.get("confirmation_status")=="CONFIRMED" and c.get("confirmed_code") not in {None,code}) for c in relevant)
    n=confirmed+negative
    precision=confirmed/n if n else None
    usable=n>=policy.minimum_historical_confirmations and precision is not None and precision>=policy.minimum_empirical_precision
    return {"evaluated_cases":n,"confirmed_cases":confirmed,"not_confirmed_cases":negative,"empirical_precision":round(precision,4) if precision is not None else None,"usable_for_confidence":usable}


def evaluate_diagnostics(cycle: dict, points: list[SignalPoint], *, compliance: dict, behavior: dict | None=None, context: dict | None=None, linked: dict | None=None, historical_cases: list[dict] | None=None, policy: DiagnosisPolicy | None=None) -> dict:
    policy=policy or DiagnosisPolicy(); linked=linked or {"qa_results":[],"maintenance_events":[],"operator_observations":[]}; historical_cases=historical_cases or []
    hypotheses=[]; confirmed=[]; detections=[]
    hydraulic=_hydraulic_signature(cycle,points)
    l2=compliance.get("overall_assessment","NOT_EVALUABLE")
    # QA outcome is an observed outcome, never a root-cause claim.
    failed_qa=[q for q in linked.get("qa_results",[]) if q.get("outcome")=="FAIL"]
    borderline=[q for q in linked.get("qa_results",[]) if q.get("outcome")=="BORDERLINE"]
    if failed_qa:
        detections.append({"code":"OUTCOME-VERIFICATION-FAIL","class":"MEASURED","severity":"HIGH","title":"Post-CIP verification failure recorded","conclusion":f"{len(failed_qa)} linked post-CIP verification result(s) are recorded as FAIL.","evidence":{"results":failed_qa}})
        if l2=="COMPLIANT":
            hypotheses.append({"code":"DX-CLEANABILITY-001","class":"INFERRED","severity":"HIGH","title":"Compliant CIP with failed verification","conclusion":"The validated process appears to have been executed, but linked verification failed. Investigate local cleanability/coverage, soil burden, verification method/location, or other causes not established by bulk CIP parameters.","confidence":"MODERATE","alternatives":["local spray/coverage limitation","hygienic-design/dead-zone issue","unmodeled soil burden","sampling/verification issue"],"recommendation":"QA/engineering review; inspect recurrent failure location and equipment cleanability before changing the validated recipe."})
    elif borderline:
        detections.append({"code":"OUTCOME-VERIFICATION-BORDERLINE","class":"MEASURED","severity":"WARNING","title":"Borderline post-CIP verification recorded","conclusion":"A linked post-CIP verification result is BORDERLINE.","evidence":{"results":borderline}})

    # Physical confirmation outranks inference.
    for m in linked.get("maintenance_events",[]):
        if m.get("confirmation_status")=="CONFIRMED" and m.get("finding_code"):
            confirmed.append({"code":m["finding_code"],"class":"CONFIRMED","severity":"HIGH","title":"Maintenance-confirmed condition","conclusion":m.get("finding_text") or f"Maintenance confirmed {m['finding_code']}.","evidence":{"maintenance_event":m}})

    if hydraulic.get("status")=="UNRELIABLE":
        detections.append({"code":"FM-INS-001","class":"DERIVED","severity":"WARNING","title":"Flow measurement unreliable","conclusion":"Flow-dependent hydraulic diagnosis is withheld because the caustic flow signal contains a suspicious flatline.","evidence":hydraulic})
    elif hydraulic.get("status")=="AVAILABLE":
        flow=hydraulic["flow_median_lpm"]; pressure=hydraulic["pressure_median_bar"]
        # Development signatures are deliberately qualitative and evidence-gated.
        # They are not universal plant limits; plant-specific L3 baselines supersede them.
        scalar_devs=(behavior or {}).get("deviations",[])
        profile_devs=(behavior or {}).get("profile_deviations",[])
        flow_low = any("return_flow_lpm" in str(d.get("feature", "")) and d.get("direction")=="LOW" for d in scalar_devs)
        pressure_high = any("return_pressure_bar" in str(d.get("feature", "")) and d.get("direction")=="HIGH" for d in scalar_devs)
        pressure_low = any("return_pressure_bar" in str(d.get("feature", "")) and d.get("direction")=="LOW" for d in scalar_devs)
        flow_profile_abnormal = any("return_flow_lpm" in str(d.get("profile", "")) for d in profile_devs)
        # If L3 is unavailable, a measured L2 flow-related deviation can establish detection of bad flow,
        # but it is not enough by itself to distinguish restriction from pump/supply causes.
        l2_flow_related = any(f.get("status")=="FAIL" and "return_flow_lpm" in " ".join(f.get("evidence",{}).get("conditions",[])) for f in compliance.get("findings",[]))
        flow_abnormal = flow_low or flow_profile_abnormal or l2_flow_related
        if flow_abnormal and flow_low and pressure_high:
            code="DX-HYD-RESTRICTION"
            support=_historical_support(code,historical_cases,policy)
            conf="HIGH" if support["usable_for_confidence"] else "MODERATE"
            hypotheses.append({"code":code,"class":"INFERRED","severity":"HIGH","title":"Possible hydraulic restriction","conclusion":"Plant-specific L3 evidence shows abnormally low return flow together with abnormally high return pressure, a signature consistent with a downstream hydraulic restriction; this is not a physical confirmation.","confidence":conf,"evidence":{"hydraulic":hydraulic,"behavior_evidence":{"flow_low":flow_low,"pressure_high":pressure_high},"historical_support":support},"alternatives":["valve-position/routing problem","pressure or flow instrumentation error","entrained air/foaming","changed circuit configuration"],"recommendation":"Inspect the affected return path/valves/spray device and record the physical finding."})
        elif flow_abnormal and flow_low and pressure_low:
            code="DX-HYD-PUMP"
            support=_historical_support(code,historical_cases,policy)
            hypotheses.append({"code":code,"class":"INFERRED","severity":"WARNING","title":"Possible pump/supply performance issue","conclusion":"Plant-specific L3 evidence shows abnormally low flow together with abnormally low pressure, which is more consistent with pump/supply performance loss than a downstream restriction.","confidence":"HIGH" if support["usable_for_confidence"] else "MODERATE","evidence":{"hydraulic":hydraulic,"behavior_evidence":{"flow_low":flow_low,"pressure_low":pressure_low},"historical_support":support},"alternatives":["suction limitation","incorrect speed command","instrumentation error","changed circuit configuration"],"recommendation":"Review pump command/speed, suction conditions, and pump performance before assuming a restriction."})
        elif l2_flow_related and not (flow_low or flow_profile_abnormal):
            detections.append({"code":"DET-HYD-FLOW","class":"DERIVED","severity":"HIGH","title":"Hydraulic flow deviation detected","conclusion":"Validated flow-related process evidence is abnormal, but plant-specific pressure/behavior evidence is insufficient to assign a root-cause hypothesis.","evidence":{"hydraulic":hydraulic}})

    # Avoid duplicating a hypothesis if maintenance already confirms the same code.
    confirmed_codes={c["code"] for c in confirmed}
    hypotheses=[h for h in hypotheses if h["code"] not in confirmed_codes][:policy.max_hypotheses]
    if confirmed:
        diagnostic_status="CONFIRMED_CONDITION"
    elif hypotheses:
        diagnostic_status="HYPOTHESES_AVAILABLE"
    elif detections:
        diagnostic_status="DETECTIONS_ONLY"
    else:
        diagnostic_status="NO_DIAGNOSTIC_FINDING"

    nodes=[]; edges=[]
    for idx,d in enumerate(detections):
        node_id=f"detection:{idx}:{d['code']}"; nodes.append({"id":node_id,"type":"DETECTION","label":d["title"],"class":d["class"]})
    for idx,h in enumerate(hypotheses):
        node_id=f"hypothesis:{idx}:{h['code']}"; nodes.append({"id":node_id,"type":"HYPOTHESIS","label":h["title"],"class":h["class"]})
        for j,d in enumerate(detections):
            if h["code"].startswith("DX-HYD") and (d["code"].startswith("DET-HYD") or d["code"]=="FM-INS-001"):
                edges.append({"from":f"detection:{j}:{d['code']}","to":node_id,"relationship":"SUPPORTS_OR_CONSTRAINS"})
            if h["code"]=="DX-CLEANABILITY-001" and d["code"]=="OUTCOME-VERIFICATION-FAIL":
                edges.append({"from":f"detection:{j}:{d['code']}","to":node_id,"relationship":"TRIGGERS_INVESTIGATION"})
    for idx,c in enumerate(confirmed):
        node_id=f"confirmed:{idx}:{c['code']}"; nodes.append({"id":node_id,"type":"CONFIRMED_CONDITION","label":c["title"],"class":c["class"]})
        for j,h in enumerate(hypotheses):
            if h["code"]==c["code"]: edges.append({"from":f"hypothesis:{j}:{h['code']}","to":node_id,"relationship":"CONFIRMED_BY_PHYSICAL_EVIDENCE"})

    return {
        "cycle_id":cycle["cycle_id"],"asset":cycle["asset"],"engine_version":ENGINE_VERSION,
        "diagnostic_status":diagnostic_status,"l2_assessment":l2,
        "detections":detections,"hypotheses":hypotheses,"confirmed_conditions":confirmed,
        "linked_evidence":linked,
        "evidence_graph":{"nodes":nodes,"edges":edges},
        "reliability_boundary":"Detections may be measured/derived. Root causes remain hypotheses unless a linked physical/maintenance record confirms them. QA failure does not identify root cause by itself.",
    }
