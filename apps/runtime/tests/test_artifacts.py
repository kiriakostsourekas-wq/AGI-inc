from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from trust_contracts import FrozenSecurityClock

from trust_runtime.api import create_app
from trust_runtime.artifacts import LocalArtifactStore, S3ArtifactStore
from trust_runtime.config import RuntimeSettings
from trust_runtime.fixtures import reference_task_contract
from trust_runtime.service import RuntimeService


class MemoryS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_kwargs) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **_kwargs) -> dict[str, object]:
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in self.objects
                if bucket == Bucket and key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }

    def delete_objects(self, *, Bucket: str, Delete: dict[str, object]) -> None:
        for item in Delete["Objects"]:
            self.objects.pop((Bucket, item["Key"]), None)


def test_artifact_signature_expiry_and_content_hash(tmp_path: Path) -> None:
    clock = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    store = LocalArtifactStore(root=tmp_path, signing_key=b"a" * 32, clock=clock)
    run_id = reference_task_contract().contract_id
    record = store.put_screenshot(
        run_id=run_id,
        content=b"synthetic-png",
        source_url="http://gomail.localhost:3001/",
        sequence_no=1,
    )
    query = store.signed_path(record, ttl_seconds=900).split("?", maxsplit=1)[1]
    parameters = dict(item.split("=", maxsplit=1) for item in query.split("&"))

    loaded, content = store.read_signed(
        run_id=run_id,
        artifact_id=record.artifact_id,
        expires=int(parameters["expires"]),
        signature=parameters["signature"],
    )
    assert content == b"synthetic-png"
    assert loaded.sha256 == record.sha256

    with pytest.raises(PermissionError, match="signature"):
        store.read_signed(
            run_id=run_id,
            artifact_id=record.artifact_id,
            expires=int(parameters["expires"]),
            signature="0" * 64,
        )

    clock.advance(timedelta(minutes=16))
    with pytest.raises(PermissionError, match="expired"):
        store.read_signed(
            run_id=run_id,
            artifact_id=record.artifact_id,
            expires=int(parameters["expires"]),
            signature=parameters["signature"],
        )


def test_s3_artifacts_preserve_hash_scope_listing_and_retention() -> None:
    clock = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    client = MemoryS3Client()
    store = S3ArtifactStore(
        bucket="trust-artifacts",
        endpoint_url="https://objects.example.test",
        access_key="synthetic-access",
        secret_key="synthetic-secret",  # noqa: S106 - in-memory test fixture
        prefix="runtime",
        signing_key=b"s" * 32,
        clock=clock,
        client=client,
    )
    run_id = reference_task_contract().contract_id
    record = store.put_screenshot(
        run_id=run_id,
        content=b"s3-synthetic-png",
        source_url="https://gomail.sandbox.example/inbox",
        sequence_no=4,
    )
    assert store.list_for_run(run_id) == [record]
    query = store.signed_path(record).split("?", maxsplit=1)[1]
    parameters = dict(item.split("=", maxsplit=1) for item in query.split("&"))
    loaded, content = store.read_signed(
        run_id=run_id,
        artifact_id=record.artifact_id,
        expires=int(parameters["expires"]),
        signature=parameters["signature"],
    )
    assert loaded.sha256 == record.sha256
    assert content == b"s3-synthetic-png"

    clock.advance(timedelta(hours=25))
    assert store.cleanup_expired() == 1
    assert store.list_for_run(run_id) == []


def test_replay_returns_recorded_frame_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_model_adapter(*_args, **_kwargs):
        pytest.fail("replay must never instantiate a model adapter")

    monkeypatch.setattr("trust_runtime.worker._agent_adapter", forbidden_model_adapter)
    settings = RuntimeSettings(app_env="test", artifact_storage_dir=tmp_path)
    service = RuntimeService(
        settings=settings,
        clock=FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC)),
    )
    app = create_app(settings=settings, service=service)
    with TestClient(app) as client:
        session = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "artifact-session-key"},
            json={},
        ).json()
        headers = {
            "Idempotency-Key": "artifact-run-key",
            "X-Demo-Session-Token": session["session_token"],
        }
        run = client.post(
            "/v1/runs",
            headers=headers,
            json={"task_contract": reference_task_contract().model_dump(mode="json")},
        ).json()
        service.record_screenshot(
            run_id=UUID(run["run_id"]),
            content=b"recorded-browser-png",
            source_url="http://gomail.localhost:3001/inbox",
        )
        before_model_events = [
            event
            for event in service.events_after(
                session_token=session["session_token"], run_id=UUID(run["run_id"])
            )
            if event.event_type == "model.usage"
        ]

        replay = client.get(
            f"/v1/runs/{run['run_id']}/replay",
            headers={"X-Demo-Session-Token": session["session_token"]},
        )
        assert replay.status_code == 200
        frame = replay.json()["frames"][0]
        assert replay.json()["label"] == "Recorded replay"
        assert replay.json()["source_execution_kind"] == "deterministic_mock"
        assert frame["app"] == "Gomail"
        assert frame["evidence"].startswith("SHA-256 ")
        after_model_events = [
            event
            for event in service.events_after(
                session_token=session["session_token"], run_id=UUID(run["run_id"])
            )
            if event.event_type == "model.usage"
        ]
        assert before_model_events == after_model_events == []

        signed_path = frame["screenshot_url"].removeprefix("/api/runtime")
        artifact = client.get(
            signed_path,
            headers={"X-Demo-Session-Token": session["session_token"]},
        )
        assert artifact.status_code == 200
        assert artifact.content == b"recorded-browser-png"
        assert artifact.headers["x-content-type-options"] == "nosniff"
