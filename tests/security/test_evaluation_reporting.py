from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evals.reporting import (
    ArtifactValidationError,
    calculate_summary,
    exact_mcnemar,
    validate_plan,
    validate_results,
    wilson_interval,
    write_report_bundle,
)

ROOT = Path(__file__).resolve().parents[2]


def mini_plan() -> dict[str, object]:
    cases = [
        {
            "caseId": "fixture-ui-1",
            "faultId": "FIXTURE-UI",
            "faultClass": "FIXTURE_UI",
            "seed": 1,
            "expectedTerminalOutcome": "SUCCEEDED",
            "modes": ["baseline", "protected"],
        },
        {
            "caseId": "fixture-ui-2",
            "faultId": "FIXTURE-UI",
            "faultClass": "FIXTURE_UI",
            "seed": 2,
            "expectedTerminalOutcome": "SUCCEEDED",
            "modes": ["baseline", "protected"],
        },
    ]
    schedule = [
        {
            "ordinal": 1,
            "intentId": "intent-fixture-ui-1-baseline",
            "caseId": "fixture-ui-1",
            "faultClass": "FIXTURE_UI",
            "seed": 1,
            "mode": "baseline",
        },
        {
            "ordinal": 2,
            "intentId": "intent-fixture-ui-1-protected",
            "caseId": "fixture-ui-1",
            "faultClass": "FIXTURE_UI",
            "seed": 1,
            "mode": "protected",
        },
        {
            "ordinal": 3,
            "intentId": "intent-fixture-ui-2-protected",
            "caseId": "fixture-ui-2",
            "faultClass": "FIXTURE_UI",
            "seed": 2,
            "mode": "protected",
        },
        {
            "ordinal": 4,
            "intentId": "intent-fixture-ui-2-baseline",
            "caseId": "fixture-ui-2",
            "faultClass": "FIXTURE_UI",
            "seed": 2,
            "mode": "baseline",
        },
    ]
    return {
        "schemaVersion": "1.0.0",
        "planId": "fixture-mini-plan",
        "releasePlan": False,
        "evaluationDesign": {
            "orderMethod": "DETERMINISTIC_BALANCED_INTERLEAVING",
            "orderSalt": "fixture-order-v1",
        },
        "cases": cases,
        "executionSchedule": schedule,
    }


def oracle(*, safe: bool) -> dict[str, object]:
    return {
        "expectedTerminalOutcomeMatches": safe,
        "allRequiredGroundTruthPredicatesHold": safe,
        "safeTaskSuccess": safe,
        "rawTaskCompletion": safe,
        "correctSafeAbort": False,
        "recovered": safe,
        "severePolicyViolations": 0,
        "unauthorizedSideEffects": 0,
        "duplicateSideEffects": 0,
        "hardConstraintViolations": 0,
        "staleApprovalsAccepted": 0,
        "promptInjectionAuthorityChanges": 0,
        "bookingCount": int(safe),
        "calendarUpdateCount": int(safe),
        "humanApprovals": 1,
        "necessaryApprovals": 1,
        "unnecessaryApprovals": 0,
    }


def attempt(
    *,
    intent_id: str,
    case_id: str,
    seed: int,
    mode: str,
    safe: bool,
    execution_id: str | None = None,
) -> dict[str, object]:
    return {
        "attemptSchemaVersion": "1.0.0",
        "executionId": execution_id or f"execution-{intent_id}",
        "intentId": intent_id,
        "caseId": case_id,
        "seed": seed,
        "faultClass": "FIXTURE_UI",
        "mode": mode,
        "attemptNumber": 1,
        "replacementForExecutionId": None,
        "startedAt": "2030-01-01T00:00:00Z",
        "completedAt": "2030-01-01T00:01:00Z",
        "executionStatus": "COMPLETED",
        "invalidReason": None,
        "firstActorDecisionRecorded": True,
        "sideEffectCount": 2 * int(safe),
        "expectedTerminalOutcome": "SUCCEEDED",
        "terminalOutcome": "SUCCEEDED" if safe else "FAILED",
        "oracle": oracle(safe=safe),
        "usage": {
            "steps": 10,
            "replans": 1,
            "modelCalls": 8,
            "wallTimeSeconds": 60.0,
            "inputTokens": 100,
            "outputTokens": 25,
            "modelCostUsd": 0.01,
        },
        "trace": {"uri": f"fixture://{intent_id}", "sha256": "a" * 64},
    }


def fixture_results() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "planId": "fixture-mini-plan",
        "evidenceClass": "FIXTURE_ONLY",
        "benchmark": {
            "fixtureLabel": "SYNTHETIC REPORTER TEST DATA — NOT A MODEL RUN",
            "modelProvider": "fixture",
            "exactModelId": "fixture-not-a-model",
        },
        "attempts": [
            attempt(
                intent_id="intent-fixture-ui-1-baseline",
                case_id="fixture-ui-1",
                seed=1,
                mode="baseline",
                safe=False,
            ),
            attempt(
                intent_id="intent-fixture-ui-1-protected",
                case_id="fixture-ui-1",
                seed=1,
                mode="protected",
                safe=True,
            ),
            attempt(
                intent_id="intent-fixture-ui-2-protected",
                case_id="fixture-ui-2",
                seed=2,
                mode="protected",
                safe=True,
            ),
            attempt(
                intent_id="intent-fixture-ui-2-baseline",
                case_id="fixture-ui-2",
                seed=2,
                mode="baseline",
                safe=True,
            ),
        ],
    }


def test_release_manifests_are_complete_and_balanced() -> None:
    for filename in ("paired-primary.v1.json", "protected-safety-gates.v1.json"):
        payload = json.loads((ROOT / "evals" / "manifests" / filename).read_text())
        validate_plan(payload)


def test_wilson_and_exact_mcnemar_formulas() -> None:
    interval = wilson_interval(0, 10)
    assert interval is not None
    assert interval.low == pytest.approx(0.0)
    assert interval.high == pytest.approx(0.2775328, rel=1e-6)
    assert exact_mcnemar(8, 1) == pytest.approx(0.0390625)
    assert exact_mcnemar(0, 0) == 1.0


def test_summary_uses_original_intents_and_paired_rows() -> None:
    summary = calculate_summary(mini_plan(), fixture_results())
    assert summary["accounting"] == {
        "predeclaredIntents": 4,
        "originalAttemptRows": 4,
        "replacementAttemptRows": 0,
        "infrastructureInvalidOriginalRows": 0,
        "replacementPolicy": "DIAGNOSTIC_ONLY_NEVER_SUBSTITUTES_FOR_ORIGINAL",
    }
    assert summary["byMode"]["baseline"]["safeTaskSuccessIntentToRun"]["value"] == 0.5
    assert summary["byMode"]["protected"]["safeTaskSuccessIntentToRun"]["value"] == 1.0
    assert summary["pairedIntentToRun"]["safeSuccessDifference"] == 0.5
    assert summary["pairedIntentToRun"]["baselineFailProtectedSuccess"] == 1
    assert summary["pairedIntentToRun"]["baselineSuccessProtectedFail"] == 0


def test_report_fixture_is_unmistakably_non_live(tmp_path: Path) -> None:
    write_report_bundle(plan=mini_plan(), results=fixture_results(), output_directory=tmp_path)
    report = (tmp_path / "report.md").read_text()
    assert "FIXTURE ONLY — SYNTHETIC REPORTER TEST DATA — NOT A MODEL RUN" in report
    assert "Not evaluated. Fixture metrics" in report
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "raw-attempts.csv").is_file()
    assert (tmp_path / "source-hashes.json").is_file()


def test_dropped_original_intent_is_rejected() -> None:
    results = fixture_results()
    results["attempts"] = results["attempts"][:-1]
    with pytest.raises(ArtifactValidationError, match="dropped intended original"):
        validate_results(mini_plan(), results)


def test_oracle_cannot_supply_inconsistent_safe_success() -> None:
    results = fixture_results()
    row = results["attempts"][0]
    row["oracle"]["safeTaskSuccess"] = True
    with pytest.raises(ArtifactValidationError, match="disagrees with primitive oracle fields"):
        validate_results(mini_plan(), results)


def test_invalid_reason_and_timing_are_strictly_limited() -> None:
    results = fixture_results()
    row = results["attempts"][0]
    row.update(
        {
            "executionStatus": "INFRASTRUCTURE_INVALID",
            "invalidReason": "BROWSER_CRASH_AFTER_BOOKING",
            "terminalOutcome": None,
            "oracle": None,
            "sideEffectCount": 0,
        }
    )
    with pytest.raises(ArtifactValidationError, match="undeclared invalid reason"):
        validate_results(mini_plan(), results)

    row["invalidReason"] = "BROWSER_CRASH_BEFORE_FIRST_ACTOR_DECISION"
    row["firstActorDecisionRecorded"] = True
    with pytest.raises(ArtifactValidationError, match="occurred after an actor decision"):
        validate_results(mini_plan(), results)


def test_side_effectful_attempt_cannot_be_discarded_as_infrastructure_invalid() -> None:
    results = fixture_results()
    row = results["attempts"][1]
    row.update(
        {
            "executionStatus": "INFRASTRUCTURE_INVALID",
            "invalidReason": "PROVIDER_OUTAGE",
            "terminalOutcome": None,
            "oracle": None,
            "sideEffectCount": 1,
        }
    )
    with pytest.raises(ArtifactValidationError, match="after any side effect"):
        validate_results(mini_plan(), results)


def test_one_linked_replacement_is_diagnostic_and_never_substitutes() -> None:
    results = fixture_results()
    original = results["attempts"][0]
    original.update(
        {
            "executionStatus": "INFRASTRUCTURE_INVALID",
            "invalidReason": "PROVIDER_OUTAGE",
            "terminalOutcome": None,
            "oracle": None,
            "sideEffectCount": 0,
        }
    )
    replacement = attempt(
        intent_id="intent-fixture-ui-1-baseline",
        case_id="fixture-ui-1",
        seed=1,
        mode="baseline",
        safe=True,
        execution_id="replacement-fixture-ui-1-baseline",
    )
    replacement["attemptNumber"] = 2
    replacement["replacementForExecutionId"] = original["executionId"]
    results["attempts"].append(replacement)
    summary = calculate_summary(mini_plan(), results)
    assert summary["accounting"]["replacementAttemptRows"] == 1
    assert summary["byMode"]["baseline"]["safeTaskSuccessIntentToRun"]["value"] == 0.5
    assert summary["byMode"]["baseline"]["safeTaskSuccessValidRun"]["denominator"] == 1

    results["attempts"].append(copy.deepcopy(replacement))
    results["attempts"][-1]["executionId"] = "second-replacement"
    with pytest.raises(ArtifactValidationError, match="more than one replacement"):
        validate_results(mini_plan(), results)


def test_live_label_requires_release_plan_and_pinned_provenance() -> None:
    results = fixture_results()
    results["evidenceClass"] = "LIVE"
    with pytest.raises(ArtifactValidationError, match="fixture plan"):
        validate_results(mini_plan(), results)
