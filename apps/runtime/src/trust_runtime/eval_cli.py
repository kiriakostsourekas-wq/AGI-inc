"""Deterministic smoke and durable evaluation-worker CLI."""

import asyncio

import typer
from trust_contracts import FrozenSecurityClock, RunState, SystemSecurityClock

from .config import RuntimeSettings
from .evaluation_worker import run_worker_loop
from .fixtures import reference_task_contract
from .service import RuntimeService
from .state_machine import RunStateMachine

app = typer.Typer(no_args_is_help=True, help="Trust runtime evaluation stubs")


@app.callback()
def root() -> None:
    """Run deterministic evaluation utilities."""


@app.command()
def smoke() -> None:
    """Validate contract hashing and a clean deterministic state path."""

    contract = reference_task_contract()
    clock = FrozenSecurityClock(contract.scenario_now)
    machine = RunStateMachine(run_id=contract.contract_id, clock=clock)
    for target in (
        RunState.ENV_RESET,
        RunState.CONTRACT_VALIDATED,
        RunState.OBSERVING,
        RunState.PLANNING,
        RunState.ACTION_PROPOSED,
        RunState.POLICY_CHECKING,
        RunState.EXECUTING,
        RunState.VERIFYING,
        RunState.FINALIZING,
    ):
        machine.transition(target, reason="deterministic smoke path")
    machine.mark_goal_verified()
    machine.transition(RunState.SUCCEEDED, reason="sealed predicates verified")
    typer.echo(f"smoke ok contract={contract.content_hash} state={machine.state.value}")


@app.command()
def worker(
    max_jobs: int = typer.Option(1, min=0, help="Zero runs continuously."),
    poll_seconds: float = typer.Option(1.0, min=0.1, max=60),
) -> None:
    """Execute queued browser intents; sealed oracle scoring runs separately."""

    settings = RuntimeSettings()
    service = RuntimeService(settings=settings, clock=SystemSecurityClock())
    try:
        completed = asyncio.run(
            run_worker_loop(
                service=service,
                max_jobs=max_jobs,
                poll_seconds=poll_seconds,
            )
        )
    finally:
        service.close()
    typer.echo(f"runtime evaluation jobs completed={completed}")
