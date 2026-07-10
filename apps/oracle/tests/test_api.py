import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from trust_oracle.config import OracleSettings
from trust_oracle.main import app


def test_health_does_not_require_oracle_credentials() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json()["service"] == "trust-oracle"


def test_score_rejects_missing_operator_token() -> None:
    response = TestClient(app).post("/internal/v1/score", json={})

    assert response.status_code == 401


def test_production_oracle_accepts_only_separate_role_https_and_secrets() -> None:
    settings = OracleSettings(
        app_env="production",
        database_url="postgresql+psycopg://eval_oracle:synthetic@db.internal/trust_runtime",
        operator_token="o" * 32,
        sandbox_admin_token="s" * 32,
        runtime_public_url="https://runtime.trust.example",
        sandbox_base_url="https://sandbox.trust.example",
    )
    assert settings.app_env == "production"


def test_production_oracle_rejects_runtime_role_and_plain_http() -> None:
    with pytest.raises(ValidationError, match="eval_oracle role"):
        OracleSettings(
            app_env="production",
            database_url="postgresql+psycopg://runtime_app:synthetic@db.internal/trust_runtime",
            operator_token="o" * 32,
            sandbox_admin_token="s" * 32,
            runtime_public_url="https://runtime.trust.example",
            sandbox_base_url="https://sandbox.trust.example",
        )
    with pytest.raises(ValidationError, match="HTTPS"):
        OracleSettings(
            app_env="production",
            database_url="postgresql+psycopg://eval_oracle:synthetic@db.internal/trust_runtime",
            operator_token="o" * 32,
            sandbox_admin_token="s" * 32,
            runtime_public_url="https://runtime.trust.example",
            sandbox_base_url="http://sandbox.localhost:3001",
        )
