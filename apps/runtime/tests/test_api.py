from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from trust_runtime.api import create_app, runtime_service_from_app
from trust_runtime.config import RuntimeSettings, StateStoreBackend


def production_settings(**overrides) -> RuntimeSettings:
    values = {
        "app_env": "production",
        "public_live_runs_enabled": True,
        "public_base_url": "https://trust.example",
        "runtime_base_url": "https://runtime.trust.example",
        "database_url": "postgresql+psycopg://runtime_app:synthetic@db.internal/trust_runtime",
        "state_store_backend": "postgres",
        "approval_hmac_secret": "a" * 32,
        "artifact_signing_secret": "b" * 32,
        "sandbox_admin_token": "c" * 32,
        "sandbox_gateway_token": "g" * 32,
        "evaluation_operator_token": "d" * 32,
        "agent_provider": "openai",
        "agent_model": "gpt-5.4-mini-2026-06-01",
        "openai_api_key": "synthetic-openai-key",
        "model_input_cost_per_million_usd": "1.00",
        "model_output_cost_per_million_usd": "4.00",
        "browser_allowed_origins": (
            "https://gomail.sandbox.example",
            "https://northstar.sandbox.example",
            "https://dayplan.sandbox.example",
        ),
        "service_allowed_hosts": ("api.openai.com", "objects.example"),
        "object_storage_backend": "s3",
        "object_storage_bucket": "trust-artifacts",
        "object_storage_endpoint": "https://objects.example",
        "object_storage_access_key": "synthetic-access-key",
        "object_storage_secret_key": "synthetic-secret-key",
        "git_commit_sha": "a" * 40,
        "browser_version": "chromium-149.0.0",
        "model_price_table_version": "openai-2026-06-01",
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_health_ready_and_actor_visible_run(contract) -> None:
    with TestClient(create_app(settings=RuntimeSettings(app_env="test"))) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/readyz").json()["status"] == "ready"

        session_response = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "session-key-0001"},
            json={"client_label": "pytest"},
        )
        assert session_response.status_code == 201
        session = session_response.json()
        replay = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "session-key-0001"},
            json={"client_label": "pytest"},
        )
        assert replay.json() == session

        run_response = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "run-key-0000001",
                "X-Demo-Session-Token": session["session_token"],
            },
            json={"task_contract": contract.model_dump(mode="json")},
        )
        assert run_response.status_code == 201
        run = run_response.json()
        serialized = str(run)
        assert "oracle_case_ref" not in serialized
        assert "fault_id" not in serialized
        assert "scenario_seed" not in serialized
        assert "expected_terminal_outcome" not in serialized
        assert run["task_contract"]["content_hash"] == contract.content_hash


def test_scenario_selection_is_sealed_from_run_response(contract) -> None:
    with TestClient(create_app(settings=RuntimeSettings(app_env="test"))) as client:
        session = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "sealed-session-key"},
            json={},
        ).json()
        response = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "sealed-run-key-01",
                "X-Demo-Session-Token": session["session_token"],
            },
            json={
                "task_contract": contract.model_dump(mode="json"),
                "scenario_selection": {
                    "scenario_id": "disrupted_trip_v1",
                    "scenario_seed": 1206,
                    "fault_id": "F-PRICE-DRIFT",
                },
            },
        )
        assert response.status_code == 201
        assert "scenario_selection" not in response.json()
        assert "F-PRICE-DRIFT" not in response.text


def test_run_rejects_contract_origin_outside_deployment_allowlist(contract) -> None:
    settings = RuntimeSettings(
        app_env="test",
        browser_allowed_origins=("http://gomail.localhost:3001",),
    )
    with TestClient(create_app(settings=settings)) as client:
        session = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "egress-session-key"},
            json={},
        ).json()
        response = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "egress-run-key-01",
                "X-Demo-Session-Token": session["session_token"],
            },
            json={"task_contract": contract.model_dump(mode="json")},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "POLICY_DENIED"


def test_public_run_cannot_select_operator_only_baseline(contract) -> None:
    with TestClient(create_app(settings=RuntimeSettings(app_env="test"))) as client:
        session = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "baseline-session-key"},
            json={},
        ).json()
        response = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "baseline-run-key-01",
                "X-Demo-Session-Token": session["session_token"],
            },
            json={
                "task_contract": contract.model_dump(mode="json"),
                "mode": "baseline",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "POLICY_DENIED"


def test_event_backlog_resumes_strictly_after_last_event_id(contract) -> None:
    app = create_app(settings=RuntimeSettings(app_env="test"))
    service = runtime_service_from_app(app)
    with TestClient(app) as client:
        session = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "events-session-key"},
            json={},
        ).json()
        token = session["session_token"]
        run = client.post(
            "/v1/runs",
            headers={
                "Idempotency-Key": "events-run-key-01",
                "X-Demo-Session-Token": token,
            },
            json={"task_contract": contract.model_dump(mode="json")},
        ).json()
        run_id = UUID(run["run_id"])
        service.append_worker_event(run_id, "worker.started", {"step": 1})
        service.append_worker_event(run_id, "observation.captured", {"step": 2})

        response = client.get(
            f"/v1/runs/{run_id}/events",
            headers={
                "X-Demo-Session-Token": token,
                "Last-Event-ID": "1",
                "Accept": "application/json",
            },
        )

    assert response.status_code == 200
    assert [event["sequence_no"] for event in response.json()["events"]] == [2, 3]


def test_idempotency_conflict_returns_versioned_error() -> None:
    with TestClient(create_app(settings=RuntimeSettings(app_env="test"))) as client:
        headers = {"Idempotency-Key": "conflicting-key"}
        first = client.post("/v1/sessions", headers=headers, json={"client_label": "one"})
        second = client.post("/v1/sessions", headers=headers, json={"client_label": "two"})
        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["version"] == "1.0.0"
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_internal_gateway_requires_credential_and_postgres() -> None:
    settings = RuntimeSettings(
        app_env="test",
        state_store_backend=StateStoreBackend.MEMORY,
        sandbox_gateway_token="g" * 32,
    )
    with TestClient(create_app(settings=settings)) as client:
        missing = client.post(
            "/internal/v1/gateway/commit",
            json={"grant_id": str(UUID(int=1)), "current_context_hash": "a" * 64},
        )
        unavailable = client.post(
            "/internal/v1/gateway/commit",
            headers={"X-Sandbox-Gateway-Token": "g" * 32},
            json={"grant_id": str(UUID(int=1)), "current_context_hash": "a" * 64},
        )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "GATEWAY_UNAUTHORIZED"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"


def test_public_production_rejects_default_secrets() -> None:
    with pytest.raises(ValidationError, match="non-default secrets"):
        RuntimeSettings(app_env="production", public_live_runs_enabled=True)


def test_public_production_requires_postgres_state() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL structured state"):
        RuntimeSettings(
            app_env="production",
            public_live_runs_enabled=True,
            approval_hmac_secret="a" * 32,
            artifact_signing_secret="b" * 32,
            sandbox_admin_token="c" * 32,
            sandbox_gateway_token="g" * 32,
            evaluation_operator_token="d" * 32,
            agent_provider="openai",
            agent_model="gpt-5.4-mini",
            openai_api_key="test-openai-key",
            model_input_cost_per_million_usd="1.00",
            model_output_cost_per_million_usd="4.00",
            state_store_backend="memory",
        )


def test_public_production_rejects_mock_provider() -> None:
    with pytest.raises(ValidationError, match="OpenAI provider"):
        RuntimeSettings(
            app_env="production",
            public_live_runs_enabled=True,
            approval_hmac_secret="a" * 32,
            artifact_signing_secret="b" * 32,
            sandbox_admin_token="c" * 32,
            sandbox_gateway_token="g" * 32,
            evaluation_operator_token="d" * 32,
            state_store_backend="postgres",
            agent_provider="mock",
        )


def test_csv_egress_allowlists_parse_from_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "BROWSER_ALLOWED_ORIGINS",
        "http://gomail.localhost:3001,http://northstar.localhost:3001",
    )
    monkeypatch.setenv("SERVICE_ALLOWED_HOSTS", "api.openai.com,telemetry.example.com")
    settings = RuntimeSettings(app_env="test")
    assert settings.browser_allowed_origins == (
        "http://gomail.localhost:3001",
        "http://northstar.localhost:3001",
    )
    assert settings.service_allowed_hosts == ("api.openai.com", "telemetry.example.com")


def test_complete_production_configuration_is_accepted() -> None:
    settings = production_settings()
    assert settings.safe_summary()["object_storage_backend"] == "s3"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"public_base_url": "http://trust.example"}, "HTTPS origin"),
        (
            {"database_url": "postgresql+psycopg://trust:synthetic@db.internal/trust_runtime"},
            "runtime_app role",
        ),
        ({"object_storage_backend": "filesystem"}, "S3-compatible"),
        ({"service_allowed_hosts": ("objects.example",)}, "required provider"),
        ({"service_allowed_hosts": ("api.openai.com",)}, "required provider"),
        ({"git_commit_sha": None}, "pinned commit"),
    ],
)
def test_production_configuration_fails_closed(overrides, message) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)
