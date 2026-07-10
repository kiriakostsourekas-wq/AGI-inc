import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from trust_runtime.browser import Observation, OpenAIResponsesAdapter
from trust_runtime.orchestrator import BudgetMeter, DecisionContextAssembler


class FakeResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.responses = FakeResponses(response)


def adapter(response: object) -> OpenAIResponsesAdapter:
    instance = OpenAIResponsesAdapter(
        model="gpt-test-pinned",
        api_key="synthetic-test-key",
        schema={},
        input_cost_per_million_usd=Decimal("2.00"),
        output_cost_per_million_usd=Decimal("8.00"),
        temperature=0.1,
        max_output_tokens=2000,
        timeout_seconds=45,
    )
    instance._client = FakeClient(response)  # pyright: ignore[reportPrivateUsage]
    return instance


@pytest.mark.asyncio
async def test_openai_adapter_accounts_actual_response_usage(booking_action) -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=250),
        output_text=booking_action.model_dump_json(),
    )
    instance = adapter(response)
    observation = Observation(
        observation_id=str(booking_action.observation_id),
        url="http://northstar.localhost:3001/",
        origin="http://northstar.localhost:3001",
        screenshot_png=b"png",
        screenshot_hash=booking_action.observation_hash,
    )
    action = await instance.decide(observation=observation, context={"budget": "bounded"})
    usage = instance.take_usage()

    assert action.action_id == booking_action.action_id
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 250
    assert usage.cost_usd == Decimal("0.004")
    with pytest.raises(RuntimeError, match="before a completed response"):
        instance.take_usage()


@pytest.mark.asyncio
async def test_openai_adapter_rejects_unmetered_response(booking_action) -> None:
    instance = adapter(
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=None, output_tokens=None),
            output_text=booking_action.model_dump_json(),
        )
    )
    observation = Observation(
        observation_id=str(booking_action.observation_id),
        url="http://northstar.localhost:3001/",
        origin="http://northstar.localhost:3001",
        screenshot_png=b"png",
        screenshot_hash=booking_action.observation_hash,
    )
    with pytest.raises(RuntimeError, match="billable token usage"):
        await instance.decide(observation=observation, context={})


@pytest.mark.asyncio
async def test_final_model_request_excludes_sealed_metadata_and_secrets(
    booking_action, contract
) -> None:
    instance = adapter(
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            output_text=booking_action.model_dump_json(),
        )
    )
    safe_context = DecisionContextAssembler(
        contract=contract,
        plan={"plan_version": 1, "goal": contract.goal, "subgoals": []},
    ).build(BudgetMeter(contract).snapshot())
    observation = Observation(
        observation_id=str(booking_action.observation_id),
        url="http://gomail.localhost:3001/",
        origin="http://gomail.localhost:3001",
        screenshot_png=b"synthetic-rendered-pixels-only",
        screenshot_hash=booking_action.observation_hash,
    )

    await instance.decide(observation=observation, context=safe_context)
    client = instance._client  # pyright: ignore[reportPrivateUsage]
    serialized = json.dumps(client.responses.calls[0], sort_keys=True)
    for forbidden in (
        "scenario_seed",
        "fault_id",
        "expected_terminal_outcome",
        "oracle_case_ref",
        "sandbox_admin_token",
        "approval_hmac_secret",
        "artifact_signing_secret",
        "OPENAI_API_KEY",
        "traveler_maya_chen.demo_card",
    ):
        assert forbidden not in serialized
    assert "input_image" in serialized
    assert "json_schema" in serialized
