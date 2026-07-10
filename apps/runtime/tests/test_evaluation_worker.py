from trust_runtime.evaluation_worker import classify_infrastructure_invalid
from trust_runtime.persistence.runtime_store import EvaluationFailureContext


def failure(
    error_type: str | None,
    *,
    actor_decisions: int = 0,
    side_effects: int = 0,
) -> EvaluationFailureContext:
    return EvaluationFailureContext(
        error_type=error_type,
        model_call_count=0,
        actor_decision_count=actor_decisions,
        side_effect_count=side_effects,
    )


def test_infrastructure_invalidity_is_narrow_and_never_discards_side_effects() -> None:
    assert classify_infrastructure_invalid(failure("APIConnectionError")) == "PROVIDER_OUTAGE"
    assert (
        classify_infrastructure_invalid(failure("TargetClosedError"))
        == "BROWSER_CRASH_BEFORE_FIRST_ACTOR_DECISION"
    )
    assert (
        classify_infrastructure_invalid(failure("OSError"))
        == "ARTIFACT_STORAGE_LOSS_BEFORE_SIDE_EFFECT"
    )
    assert classify_infrastructure_invalid(failure("ValueError")) is None
    assert classify_infrastructure_invalid(failure("APIConnectionError", actor_decisions=1)) is None
    assert classify_infrastructure_invalid(failure("TargetClosedError", side_effects=1)) is None
