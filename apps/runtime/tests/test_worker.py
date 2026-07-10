from datetime import UTC, datetime

import pytest
from trust_contracts import FrozenSecurityClock, RunMode, RunState

from trust_runtime.config import RuntimeSettings
from trust_runtime.effects import EffectDeriver
from trust_runtime.fixtures import reference_task_contract
from trust_runtime.orchestrator import MemoryEventSink
from trust_runtime.policy import DeterministicPolicyEngine, PolicyContext
from trust_runtime.service import RuntimeService
from trust_runtime.state_machine import RunStateMachine
from trust_runtime.worker import BaselineApprovalBroker, run_browser_worker, sandbox_app_urls


def test_shared_sandbox_origin_maps_apps_to_paths() -> None:
    assert sandbox_app_urls(("https://sandbox.example",)) == {
        "gomail": "https://sandbox.example/gomail",
        "northstar": "https://sandbox.example/northstar",
        "dayplan": "https://sandbox.example/dayplan",
    }


class FailingChromium:
    async def launch(self, **_kwargs):
        raise RuntimeError("synthetic browser launch failure")


class FailingPlaywright:
    chromium = FailingChromium()

    async def stop(self) -> None:
        return None


class FailingPlaywrightContext:
    async def start(self) -> FailingPlaywright:
        return FailingPlaywright()


class BaselineFixtureService:
    def __init__(self, *, approved: bool, machine: RunStateMachine) -> None:
        self.settings = RuntimeSettings(app_env="test")
        self.approved = approved
        self.calls = 0
        self.machine = machine

    def evaluation_approval_decision(self, *, effect) -> bool:
        self.calls += 1
        return self.approved

    def run_machine(self, _run_id):
        return self.machine


@pytest.mark.asyncio
async def test_browser_worker_failure_persists_terminal_transition(monkeypatch) -> None:
    service = RuntimeService(
        settings=RuntimeSettings(app_env="test"),
        clock=FrozenSecurityClock(datetime(2030, 6, 13, 16, 0, tzinfo=UTC)),
    )
    session = service.create_session()
    run = service.create_run(
        session_token=session.session_token,
        contract=reference_task_contract(),
    )
    monkeypatch.setattr(
        "trust_runtime.worker.async_playwright",
        lambda: FailingPlaywrightContext(),
    )

    state = await run_browser_worker(service=service, run_id=run.run_id)

    assert state is RunState.FAILED
    current = service.get_run(
        session_token=session.session_token,
        run_id=run.run_id,
    )
    assert current.status is RunState.FAILED
    events = service.events_after(
        session_token=session.session_token,
        run_id=run.run_id,
    )
    assert [event.event_type for event in events[-2:]] == [
        "worker.failed",
        "run.state_transition",
    ]
    assert events[-1].payload["to_state"] == RunState.FAILED.value


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_baseline_broker_uses_same_human_fixture_before_binding(
    monkeypatch, approved, contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract=contract))
    machine = RunStateMachine(run_id=booking_action.run_id, clock=clock)
    service = BaselineFixtureService(approved=approved, machine=machine)
    events = MemoryEventSink()
    bindings: list[object] = []

    async def record_binding(**kwargs) -> None:
        bindings.append(kwargs)

    monkeypatch.setattr("trust_runtime.worker.bind_sandbox_approval", record_binding)
    broker = BaselineApprovalBroker(
        service=service,
        page=object(),
        northstar_origin=contract.allowed_origins[1],
        events=events,
    )

    result = await broker.authorize(
        effect=effect,
        decision=decision,
        contract_hash=contract.content_hash,
    )

    assert service.calls == 1
    assert bool(bindings) is approved
    assert (result is not None) is approved
    assert events.events[-1][0] == ("approval.approved" if approved else "approval.rejected")


def test_evaluation_human_fixture_accepts_only_exact_compliant_prices(
    contract, booking_action, booking_target, clock
) -> None:
    service = RuntimeService(settings=RuntimeSettings(app_env="test"), clock=clock)
    handle = service.create_evaluation_run(
        contract=contract,
        mode=RunMode.BASELINE,
        scenario_id="disrupted_trip_v1",
        scenario_seed=1201,
        fault_id="PRICE_DRIFT",
        expected_terminal_outcome=RunState.SUCCEEDED,
    )
    action = booking_action.model_copy(update={"run_id": handle.run.run_id})
    approved_effect = EffectDeriver().derive(
        action=action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    expensive_context = booking_target.context.model_copy(
        update={"base_fare_minor": 44_000, "total_additional_cost_minor": 47_900}
    )
    expensive_effect = EffectDeriver().derive(
        action=action,
        contract=contract,
        trusted_target=booking_target.model_copy(update={"context": expensive_context}),
        derived_at=clock.now(),
    )

    assert service.evaluation_approval_decision(effect=approved_effect) is True
    assert service.evaluation_approval_decision(effect=expensive_effect) is False
    service.close()
