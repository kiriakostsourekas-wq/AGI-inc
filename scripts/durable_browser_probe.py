"""Drive and verify the PostgreSQL-authoritative rendered browser workflow."""

import argparse
import json
import time
from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from trust_contracts import TERMINAL_RUN_STATES
from trust_runtime.fixtures import reference_task_contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-base-url", default="http://127.0.0.1:8100")
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://runtime_app:trust_runtime@localhost:5432/trust_runtime",
    )
    parser.add_argument("--timeout-seconds", type=int, default=90)
    return parser.parse_args()


def drive(*, runtime_base_url: str, timeout_seconds: int) -> dict[str, object]:
    with httpx.Client(base_url=runtime_base_url, timeout=15) as client:
        session = (
            client.post(
                "/v1/sessions",
                headers={"Idempotency-Key": f"durable-session-{uuid4()}"},
                json={"client_label": "durable-browser-probe"},
            )
            .raise_for_status()
            .json()
        )
        token = str(session["session_token"])
        run = (
            client.post(
                "/v1/runs",
                headers={
                    "Idempotency-Key": f"durable-run-{uuid4()}",
                    "X-Demo-Session-Token": token,
                },
                json={"task_contract": reference_task_contract().model_dump(mode="json")},
            )
            .raise_for_status()
            .json()
        )
        run_id = str(run["run_id"])
        approved = False
        deadline = time.monotonic() + timeout_seconds
        current: dict[str, Any] = run
        while time.monotonic() < deadline:
            current = cast(
                dict[str, Any],
                client.get(
                    f"/v1/runs/{run_id}",
                    headers={"X-Demo-Session-Token": token},
                )
                .raise_for_status()
                .json(),
            )
            pending = current.get("pending_approval")
            if isinstance(pending, dict) and not approved:
                approval = cast(dict[str, object], pending)
                client.post(
                    f"/v1/approvals/{approval['approval_id']}/approve",
                    headers={
                        "X-Demo-Session-Token": token,
                        "Idempotency-Key": f"durable-approval-{uuid4()}",
                        "If-Match": str(approval["approved_context_hash"]),
                    },
                ).raise_for_status()
                approved = True
            if current.get("status") in {state.value for state in TERMINAL_RUN_STATES}:
                break
            time.sleep(0.2)
        if current.get("status") != "SUCCEEDED":
            raise RuntimeError(f"durable browser run ended as {current.get('status')}")
        replay = (
            client.get(
                f"/v1/runs/{run_id}/replay",
                headers={"X-Demo-Session-Token": token},
            )
            .raise_for_status()
            .json()
        )
        events = (
            client.get(
                f"/v1/runs/{run_id}/events",
                headers={"X-Demo-Session-Token": token, "Accept": "application/json"},
            )
            .raise_for_status()
            .json()["events"]
        )
        return {
            "run_id": run_id,
            "status": current["status"],
            "approved": approved,
            "replay_frames": len(replay["frames"]),
            "event_count": len(events),
            "model_usage_events": sum(event["event_type"] == "model.usage" for event in events),
        }


def database_evidence(*, database_url: str, run_id: str) -> tuple[object, ...]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    select r.status, ag.status, ep.status, se.status,
                           se.verified_at is not null, rb.verified_at is not null,
                           rb.status,
                           (select count(*) from sandbox.replacement_bookings x
                             where x.run_id = r.id),
                           (select count(*) from runtime.side_effects y
                             where y.run_id = r.id)
                      from runtime.runs r
                      join runtime.approval_grants ag on ag.run_id = r.id
                      join runtime.effect_proposals ep on ep.id = ag.effect_proposal_id
                      join runtime.side_effects se on se.run_id = r.id
                      join sandbox.replacement_bookings rb on rb.run_id = r.id
                     where r.id = :run_id
                    """
                ),
                {"run_id": run_id},
            ).one()
    finally:
        engine.dispose()
    evidence = tuple(row)
    expected = ("SUCCEEDED", "CONSUMED", "COMMITTED", "VERIFIED", True, True, "confirmed", 1, 1)
    if evidence != expected:
        raise RuntimeError(f"durable database evidence mismatch: {evidence!r}")
    return evidence


def main() -> None:
    args = parse_args()
    result = drive(
        runtime_base_url=str(args.runtime_base_url),
        timeout_seconds=int(args.timeout_seconds),
    )
    result["database_evidence"] = list(
        database_evidence(database_url=str(args.database_url), run_id=str(result["run_id"]))
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
