import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from trust_contracts import (
    CanonicalizationError,
    FixedScenarioClock,
    FrozenSecurityClock,
    Money,
    TaskContract,
    jcs_canonicalize,
    sha256_hex,
    uuid7,
)


def test_jcs_is_order_independent_and_rejects_floats() -> None:
    left = {"z": [3, 2, 1], "a": {"currency": "USD", "amount": "450.00"}}
    right = {"a": {"amount": "450.00", "currency": "USD"}, "z": [3, 2, 1]}
    assert jcs_canonicalize(left) == jcs_canonicalize(right)
    assert sha256_hex(left) == sha256_hex(right)
    with pytest.raises(CanonicalizationError, match="floating-point"):
        jcs_canonicalize({"unsafe": 0.1})


def test_jcs_uses_utf16_key_order() -> None:
    # U+1F600 sorts by its UTF-16 surrogate pair before U+E000 under RFC 8785.
    encoded = jcs_canonicalize({"\ue000": 1, "😀": 2}).decode()
    assert encoded == '{"😀":2,"\ue000":1}'


def test_shared_cross_language_jcs_hash_and_signature_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "contracts"
        / "fixtures"
        / "jcs-golden.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    canonical = jcs_canonicalize(fixture["value"])
    assert canonical.decode() == fixture["canonical"]
    assert hashlib.sha256(canonical).hexdigest() == fixture["sha256"]
    signature = hmac.new(
        bytes.fromhex(fixture["hmac_key_hex"]), canonical, hashlib.sha256
    ).hexdigest()
    assert signature == fixture["hmac_sha256"]


def test_money_is_exact_and_serializes_as_minor_units() -> None:
    amount = Money(amount=Decimal("450"), currency="usd")
    assert amount.minor_units == 45_000
    assert amount.model_dump(mode="json") == {"amount_minor": 45_000, "currency": "USD"}
    with pytest.raises(ValidationError, match="float"):
        Money(amount=0.1, currency="USD")
    with pytest.raises(ValidationError, match="fractional"):
        Money(amount="1.001", currency="USD")


def test_task_contract_hash_detects_tampering(contract: TaskContract) -> None:
    payload = contract.model_dump(mode="json")
    payload["goal"] = "Tampered goal"
    with pytest.raises(ValidationError, match="content_hash"):
        TaskContract.model_validate(payload)


def test_actor_contract_has_no_sealed_fields(contract: TaskContract) -> None:
    payload = contract.actor_payload()
    forbidden = {
        "scenario_id",
        "scenario_seed",
        "fault_id",
        "oracle_case_ref",
        "expected_terminal_outcome",
        "manifest_hash",
    }
    assert forbidden.isdisjoint(payload)


def test_scenario_and_security_clocks_are_distinct() -> None:
    scenario = FixedScenarioClock(datetime(2030, 6, 13, 9, 0, tzinfo=UTC))
    security = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    assert scenario.now().year == 2030
    assert security.now().year == 2026


def test_uuid7_version_and_variant() -> None:
    identifier = uuid7(timestamp_ms=1_700_000_000_000, random_bits=1234)
    assert identifier.version == 7
    assert identifier.variant == "specified in RFC 4122"
