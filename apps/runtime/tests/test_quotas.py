from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from trust_contracts import FrozenSecurityClock

from trust_runtime.api import create_app
from trust_runtime.config import RuntimeSettings
from trust_runtime.errors import QuotaExceededError
from trust_runtime.quotas import PublicQuotaGuard


def test_concurrent_quota_releases_capacity() -> None:
    clock = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    quota = PublicQuotaGuard(clock=clock, maximum_concurrent=1, maximum_per_ip_per_hour=3)

    quota.reserve("192.0.2.1")
    with pytest.raises(QuotaExceededError, match="capacity"):
        quota.reserve("192.0.2.2")

    quota.release()
    quota.reserve("192.0.2.2")
    assert quota.active == 1


def test_hourly_quota_uses_a_rolling_window() -> None:
    clock = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    quota = PublicQuotaGuard(clock=clock, maximum_concurrent=2, maximum_per_ip_per_hour=1)

    quota.reserve("192.0.2.1")
    quota.release()
    with pytest.raises(QuotaExceededError, match="hourly"):
        quota.reserve("192.0.2.1")

    clock.advance(timedelta(hours=1, seconds=1))
    quota.reserve("192.0.2.1")
    assert quota.active == 1


def test_request_body_over_256_kib_is_rejected_before_parsing() -> None:
    app = create_app(settings=RuntimeSettings(app_env="test"))
    with TestClient(app) as client:
        response = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "oversized-request-key"},
            content=b"x" * (256 * 1024 + 1),
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
