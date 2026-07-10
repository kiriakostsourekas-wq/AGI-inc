"""Durable runtime-side execution worker for predeclared evaluation intents."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from trust_contracts import RunMode, RunState

from .fixtures import reference_task_contract
from .persistence.runtime_store import (
    ClaimedEvaluationExecution,
    EvaluationFailureContext,
)
from .service import RuntimeService
from .worker import run_browser_worker

_PROVIDER_FAILURES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }
)
_BROWSER_FAILURES = frozenset(
    {
        "BrowserTypeLaunchError",
        "TargetClosedError",
    }
)
_ARTIFACT_FAILURES = frozenset({"ArtifactAccessError", "OSError"})


def classify_infrastructure_invalid(context: EvaluationFailureContext) -> str | None:
    """Apply only the three predeclared invalid reasons, before any side effect."""

    if context.error_type is None or context.side_effect_count > 0:
        return None
    if context.error_type in _PROVIDER_FAILURES and context.actor_decision_count == 0:
        return "PROVIDER_OUTAGE"
    if context.error_type in _BROWSER_FAILURES and context.actor_decision_count == 0:
        return "BROWSER_CRASH_BEFORE_FIRST_ACTOR_DECISION"
    if context.error_type in _ARTIFACT_FAILURES and context.actor_decision_count == 0:
        return "ARTIFACT_STORAGE_LOSS_BEFORE_SIDE_EFFECT"
    return None


@dataclass(slots=True)
class EvaluationExecutionWorker:
    service: RuntimeService
    worker_id: str

    async def process_one(self) -> bool:
        store = self.service.store
        if store is None:
            raise RuntimeError("evaluation worker requires PostgreSQL state")
        claim = store.claim_evaluation_execution(
            worker_id=self.worker_id,
            claimed_at=self.service.clock.now(),
            maximum_run_cost_usd=Decimal(self.service.settings.run_max_model_cost_usd),
        )
        if claim is None:
            return False
        await self._execute(claim)
        return True

    async def _execute(self, claim: ClaimedEvaluationExecution) -> None:
        store = self.service.store
        assert store is not None
        run_id: UUID | None = None
        try:
            case = claim.case_manifest
            scenario_id = case.get("scenarioId", "disrupted_trip_v1")
            seed = case.get("seed")
            fault_id = case.get("faultId")
            expected_outcome = case.get("expectedTerminalOutcome")
            if not isinstance(scenario_id, str) or not isinstance(seed, int):
                raise RuntimeError("evaluation case is missing scenario or seed")
            if fault_id is not None and not isinstance(fault_id, str):
                raise RuntimeError("evaluation case faultId is malformed")
            if not isinstance(expected_outcome, str):
                raise RuntimeError("evaluation case expected outcome is malformed")
            handle = self.service.create_evaluation_run(
                contract=reference_task_contract(),
                mode=RunMode(claim.arm),
                scenario_id=scenario_id,
                scenario_seed=seed,
                fault_id=fault_id,
                expected_terminal_outcome=RunState(expected_outcome),
            )
            run_id = handle.run.run_id
            store.attach_evaluation_run(execution_id=claim.execution_id, run_id=run_id)
            await run_browser_worker(service=self.service, run_id=run_id)
            failure = store.evaluation_failure_context(run_id)
            invalid_reason = classify_infrastructure_invalid(failure)
            store.finish_evaluation_runtime(
                job_id=claim.job_id,
                worker_id=self.worker_id,
                execution_id=claim.execution_id,
                run_id=run_id,
                finished_at=self.service.clock.now(),
                infrastructure_invalid_reason=invalid_reason,
            )
        except Exception as error:
            store.fail_evaluation_execution(
                job_id=claim.job_id,
                worker_id=self.worker_id,
                execution_id=claim.execution_id,
                error=f"{type(error).__name__}: {error}",
                finished_at=self.service.clock.now(),
            )
            raise
        finally:
            if run_id is not None:
                self.service.release_evaluation_run(run_id)


def new_worker_id() -> str:
    return f"evaluation-runtime-{secrets.token_hex(8)}"


async def run_worker_loop(
    *,
    service: RuntimeService,
    max_jobs: int,
    poll_seconds: float,
) -> int:
    if max_jobs < 0:
        raise ValueError("max_jobs must be zero (continuous) or positive")
    if not 0.1 <= poll_seconds <= 60:
        raise ValueError("poll_seconds must be between 0.1 and 60")
    worker = EvaluationExecutionWorker(service=service, worker_id=new_worker_id())
    completed = 0
    while max_jobs == 0 or completed < max_jobs:
        claimed = await worker.process_one()
        if claimed:
            completed += 1
            continue
        if max_jobs > 0:
            break
        await asyncio.sleep(poll_seconds)
    return completed
