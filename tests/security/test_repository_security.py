from __future__ import annotations

from pathlib import Path

from scripts.security_audit import (
    check_egress_allowlists,
    check_prompt_injection_corpus,
    check_security_test_map,
    scan_client_secret_references,
    scan_secret_patterns,
)

ROOT = Path(__file__).resolve().parents[2]


def test_no_credential_shaped_values_in_authored_files() -> None:
    result = scan_secret_patterns(ROOT)
    assert result.status == "PASS", result.detail


def test_client_modules_do_not_reference_server_secret_variables() -> None:
    result = scan_client_secret_references(ROOT)
    assert result.status == "PASS", result.detail


def test_browser_and_service_egress_allowlists_are_exact_and_separate() -> None:
    result = check_egress_allowlists(ROOT)
    assert result.status == "PASS", result.detail


def test_prompt_injection_corpus_covers_all_predeclared_safety_seeds() -> None:
    result = check_prompt_injection_corpus(ROOT)
    assert result.status == "PASS", result.detail


def test_replay_idempotency_and_leakage_tests_have_traceable_owners() -> None:
    result = check_security_test_map(ROOT)
    assert result.status == "PASS", result.detail
