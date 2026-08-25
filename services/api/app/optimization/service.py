from __future__ import annotations

from pathlib import Path

from .models import ControlledTrialAssessmentRequest, OptimizationDecisionRecord, OptimizationPolicy
from .engine import assess_controlled_trial
from .store import ImmutableOptimizationStore


class OptimizationService:
    """Persistence/governance shell for L6 artifacts.

    Discovery can be performed from live analysis pipelines; once a candidate is
    material enough for plant review it is frozen here as immutable evidence.
    """
    def __init__(self, runtime_root: Path):
        self.store = ImmutableOptimizationStore(runtime_root / "optimization")

    def save_candidate(self, candidate: dict) -> dict:
        return self.store.save("candidates", candidate["candidate_id"], candidate)

    def save_decision(self, decision: OptimizationDecisionRecord) -> dict:
        # Append-only by decision_ref so later plant decisions do not overwrite history.
        return self.store.save("decisions", decision.decision_ref, decision.model_dump(mode="json"))

    def assess_trial(self, request: ControlledTrialAssessmentRequest, policy: OptimizationPolicy | None = None) -> dict:
        candidate = self.store.get("candidates", request.candidate_id)
        assessment = assess_controlled_trial(
            candidate, request.results, policy,
            engineering_approval_ref=request.engineering_approval_ref,
            qa_approval_ref=request.qa_approval_ref,
            protocol_ref=request.protocol_ref,
        )
        key=f"{request.candidate_id}-{len(request.results)}-{assessment['assessment']}"
        return self.store.save("trial_assessments", key, assessment)
