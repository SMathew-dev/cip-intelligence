from __future__ import annotations
import hashlib, json, os, stat
from pathlib import Path
from app.reconstruction.service import load_normalized_jsonl
from .engine import ENGINE_VERSION, evaluate_diagnostics, link_evidence
from .models import QAResult, MaintenanceEvent, OperatorObservation, DiagnosticCase, DiagnosisPolicy
from .store import JsonRecordStore

class DiagnosticService:
    def __init__(self, runtime_root: Path):
        self.runtime_root=runtime_root
        self.qa_store=JsonRecordStore(runtime_root/"diagnostics"/"qa", "result_id")
        self.maintenance_store=JsonRecordStore(runtime_root/"diagnostics"/"maintenance", "event_id")
        self.observation_store=JsonRecordStore(runtime_root/"diagnostics"/"observations", "observation_id")
        self.case_store=JsonRecordStore(runtime_root/"diagnostics"/"cases", "case_id")
        self.output_root=runtime_root/"diagnostics"/"evaluations"; self.output_root.mkdir(parents=True,exist_ok=True)
        self.normalized_root=runtime_root/"normalized"; self.reconstruction_root=runtime_root/"reconstructions"; self.compliance_root=runtime_root/"compliance"; self.behavior_root=runtime_root/"behavior"/"evaluations"; self.context_root=runtime_root/"context"/"evaluations"

    @staticmethod
    def _latest(directory:Path, *, required:bool=True):
        ps=sorted(directory.glob("*.json"),key=lambda p:p.stat().st_mtime)
        if not ps:
            if required: raise FileNotFoundError(f"No artifact exists in {directory}")
            return None,None
        p=ps[-1]; return p,json.loads(p.read_text(encoding="utf-8"))

    def save_qa(self,x:QAResult):return self.qa_store.save(x)
    def save_maintenance(self,x:MaintenanceEvent):return self.maintenance_store.save(x)
    def save_observation(self,x:OperatorObservation):return self.observation_store.save(x)
    def save_case(self,x:DiagnosticCase):return self.case_store.save(x)

    def evaluate_ingestion(self,ingestion_id:str,policy:DiagnosisPolicy|None=None)->dict:
        policy=policy or DiagnosisPolicy(); records=self.normalized_root/ingestion_id/"records.jsonl"
        if not records.exists():raise FileNotFoundError(f"Normalized ingestion {ingestion_id!r} was not found.")
        rp,recon=self._latest(self.reconstruction_root/ingestion_id); cp,compliance=self._latest(self.compliance_root/ingestion_id)
        bp,behavior=self._latest(self.behavior_root/ingestion_id,required=False); xp,context=self._latest(self.context_root/ingestion_id,required=False)
        points=load_normalized_jsonl(records); cycles={c["cycle_id"]:c for c in recon.get("result",{}).get("cycles",[])}
        bmap={c.get("cycle_id"):c for c in (behavior or {}).get("cycles",[])}; xmap={c.get("cycle_id"):c for c in (context or {}).get("cycles",[])}
        qa=[QAResult.model_validate(x) for x in self.qa_store.list()]; m=[MaintenanceEvent.model_validate(x) for x in self.maintenance_store.list()]; o=[OperatorObservation.model_validate(x) for x in self.observation_store.list()]; cases=self.case_store.list()
        results=[]
        for cr in compliance.get("cycles",[]):
            cycle=cycles.get(cr["cycle_id"])
            if not cycle:continue
            linked=link_evidence(cycle,qa,m,o,policy)
            results.append(evaluate_diagnostics(cycle,points,compliance=cr,behavior=bmap.get(cycle["cycle_id"]),context=xmap.get(cycle["cycle_id"]),linked=linked,historical_cases=cases,policy=policy))
        lineage={"normalized_sha256":hashlib.sha256(records.read_bytes()).hexdigest(),"reconstruction_sha256":hashlib.sha256(rp.read_bytes()).hexdigest(),"compliance_sha256":hashlib.sha256(cp.read_bytes()).hexdigest(),"behavior_sha256":hashlib.sha256(bp.read_bytes()).hexdigest() if bp else None,"context_sha256":hashlib.sha256(xp.read_bytes()).hexdigest() if xp else None,"qa_store_sha256":hashlib.sha256(json.dumps(self.qa_store.list(),sort_keys=True).encode()).hexdigest(),"maintenance_store_sha256":hashlib.sha256(json.dumps(self.maintenance_store.list(),sort_keys=True).encode()).hexdigest(),"observation_store_sha256":hashlib.sha256(json.dumps(self.observation_store.list(),sort_keys=True).encode()).hexdigest(),"diagnostic_case_store_sha256":hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest()}
        key=hashlib.sha256(json.dumps({"engine":ENGINE_VERSION,"lineage":lineage,"policy":policy.model_dump(mode="json")},sort_keys=True).encode()).hexdigest()[:20]
        outdir=self.output_root/ingestion_id;outdir.mkdir(parents=True,exist_ok=True);target=outdir/f"{ENGINE_VERSION}-{key}.json"
        if target.exists():
            d=json.loads(target.read_text(encoding="utf-8"));d["duplicate"]=True;d["artifact_path"]=str(target);return d
        artifact={"ingestion_id":ingestion_id,"engine":"cip-diagnostic-intelligence","engine_version":ENGINE_VERSION,"cycles":results,"lineage":lineage,"duplicate":False}
        tmp=target.with_suffix(".tmp");tmp.write_text(json.dumps(artifact,indent=2,sort_keys=True),encoding="utf-8");os.replace(tmp,target);target.chmod(target.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH);artifact["artifact_path"]=str(target);return artifact
