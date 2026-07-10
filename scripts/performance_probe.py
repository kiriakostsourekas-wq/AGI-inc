"""Generate local component-latency evidence without claiming deployed performance."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    FrozenSecurityClock,
    ToolName,
    TrustedTargetKind,
    uuid7,
)
from trust_runtime.api import create_app
from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.config import RuntimeSettings, StateStoreBackend
from trust_runtime.effects import EffectDeriver, TrustedTargetDescriptor
from trust_runtime.fixtures import reference_task_contract
from trust_runtime.service import RuntimeService


def p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def measure(iterations: int, operation) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000)
    return samples


def booking_effect(clock: FrozenSecurityClock):
    contract = reference_task_contract()
    action = ActionProposal(
        run_id=uuid7(),
        step_number=1,
        plan_version=1,
        observation_id=uuid7(),
        observation_hash="a" * 64,
        tool=ToolName.CLICK,
        target_description="Confirm exact replacement",
        coordinates_normalized=(720, 810),
        grounding_confidence=Decimal("0.95"),
        decision_summary="Confirm exact approved synthetic itinerary.",
    )
    target = TrustedTargetDescriptor(
        target_kind=TrustedTargetKind.BOOKING_CONFIRM,
        origin="http://northstar.localhost:3001",
        trusted_target_id="northstar.confirm-rebooking",
        context=BookingCommitContext(
            traveler_id="traveler_maya_chen",
            reservation_id="NST-PERF",
            offer_version="offer-v1",
            marketing_carrier="Northstar Air",
            operating_carrier="Northstar Air",
            flight_id="NS451",
            origin_airport="SFO",
            destination_airport="SEA",
            departure=datetime(2030, 6, 14, 21, 10, tzinfo=UTC),
            arrival=datetime(2030, 6, 14, 23, 15, tzinfo=UTC),
            stop_count=0,
            cabin="economy",
            fare_class="Y",
            seat_type="aisle",
            base_fare_minor=35_000,
            taxes_and_fees_minor=3_900,
            total_additional_cost_minor=38_900,
            currency="USD",
        ),
    )
    return contract, EffectDeriver().derive(
        action=action,
        contract=contract,
        trusted_target=target,
        derived_at=clock.now(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args()
    if args.iterations < 20:
        raise SystemExit("at least 20 iterations are required")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://runtime_app:trust_runtime@localhost:5432/trust_runtime",
    )
    clock = FrozenSecurityClock(datetime.now(UTC))
    with TemporaryDirectory(prefix="trust-performance-") as artifact_dir:
        settings = RuntimeSettings(
            app_env="test",
            database_url=database_url,
            state_store_backend=StateStoreBackend.POSTGRES,
            artifact_storage_dir=Path(artifact_dir),
        )
        service = RuntimeService(settings=settings, clock=clock)
        app = create_app(settings=settings, service=service)
        with TestClient(app) as client:
            health_samples = measure(
                args.iterations,
                lambda: client.get("/healthz").raise_for_status(),
            )
            session = service.create_session()
            run = service.create_run(
                session_token=session.session_token,
                contract=reference_task_contract(),
            )
            service.record_screenshot(
                run_id=run.run_id,
                content=b"synthetic-local-performance-frame",
                source_url="http://gomail.localhost:3001/inbox",
            )
            replay_samples = measure(
                args.iterations,
                lambda: client.get(
                    f"/v1/runs/{run.run_id}/replay",
                    headers={"X-Demo-Session-Token": session.session_token},
                ).raise_for_status(),
            )
            event_samples: list[float] = []
            cursor = len(
                service.events_after(
                    session_token=session.session_token,
                    run_id=run.run_id,
                    after=0,
                )
            )
            for index in range(args.iterations):
                started = perf_counter()
                service.append_worker_event(run.run_id, "performance.probe", {"index": index})
                visible = service.events_after(
                    session_token=session.session_token,
                    run_id=run.run_id,
                    after=cursor,
                )
                if len(visible) != 1:
                    raise RuntimeError("persisted event was not immediately visible")
                cursor = visible[0].sequence
                event_samples.append((perf_counter() - started) * 1000)

        contract, effect = booking_effect(clock)
        authority = ApprovalAuthority(
            signing_key=b"local-performance-approval-key-32-bytes",
            clock=clock,
            default_ttl_seconds=180,
        )
        request = authority.request(effect=effect, summary="Local approval latency probe")
        grant = authority.approve(
            request_id=request.request_id,
            contract_hash=contract.content_hash,
        )
        approval_samples = measure(
            args.iterations,
            lambda: authority.validate(
                grant=grant,
                effect=effect,
                contract_hash=contract.content_hash,
            ),
        )

    metrics = {
        "api_health_p95_ms": p95(health_samples),
        "persisted_event_visibility_p95_ms": p95(event_samples),
        "approval_validation_p95_ms": p95(approval_samples),
        "replay_response_p95_ms": p95(replay_samples),
    }
    thresholds = {
        "api_health_p95_ms": 250,
        "persisted_event_visibility_p95_ms": 1000,
        "approval_validation_p95_ms": 200,
        "replay_response_p95_ms": 2000,
    }
    report = {
        "schema_version": "1.0.0",
        "evidence_scope": "LOCAL_COMPONENT_ONLY",
        "generated_at": datetime.now(UTC).isoformat(),
        "iterations": args.iterations,
        "metrics": metrics,
        "thresholds": thresholds,
        "passes": {name: metrics[name] < limit for name, limit in thresholds.items()},
        "limitations": [
            "Does not measure deployed landing-page LCP.",
            "Replay timing covers the API bundle response, not remote route rendering.",
            "Runtime loop overhead and production concurrency require deployed load evidence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if all(report["passes"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
