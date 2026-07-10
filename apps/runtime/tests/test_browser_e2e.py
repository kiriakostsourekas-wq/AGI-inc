import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    EffectProposal,
    FrozenSecurityClock,
    RunState,
    TaskContract,
    ToolName,
    uuid7,
)

from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.browser import (
    BrowserExecutor,
    BrowserPage,
    Observation,
    ScreenshotObserver,
    install_browser_egress_guard,
)
from trust_runtime.fixtures import reference_task_contract
from trust_runtime.orchestrator import (
    AgentOrchestrator,
    DecisionContextAssembler,
    InMemoryApprovalBroker,
    MemoryEventSink,
)
from trust_runtime.policy import DeterministicPolicyEngine
from trust_runtime.sandbox_bridge import (
    PageWithContext,
    SandboxTargetResolver,
    bind_sandbox_approval,
)
from trust_runtime.state_machine import RunStateMachine
from trust_runtime.verification import VisibleTextVerifier

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.getenv("TRUST_RUN_BROWSER_TESTS") != "1",
        reason="set TRUST_RUN_BROWSER_TESTS=1 with a built sandbox on port 3101",
    ),
]
_SANDBOX_ADMIN_TOKEN = "local-sandbox-admin"  # noqa: S105 - synthetic local fixture


class CoordinateMockAdapter:
    """Deterministic rendered-UI smoke adapter; always recorded as mock, never live."""

    def __init__(self, *, run_id, sandbox_port: int) -> None:
        self.run_id = run_id
        self.calls = 0
        self.actions: list[tuple[ToolName, tuple[int, int] | None, str | None, str]] = [
            (
                ToolName.OPEN_URL,
                None,
                f"http://northstar.localhost:{sandbox_port}/?run={run_id}",
                "Open the rendered airline application.",
            ),
            (ToolName.CLICK, (793, 571), None, "Search the rendered replacement options."),
            (ToolName.SCROLL, None, "down", "Scroll the rendered itinerary review."),
            (ToolName.CLICK, (816, 863), None, "Review the preferred compliant nonstop option."),
            (ToolName.SCROLL, None, "down", "Continue to the rendered confirmation control."),
            (ToolName.CLICK, (805, 857), None, "Confirm the exact paused rebooking effect."),
            (
                ToolName.OPEN_URL,
                None,
                f"http://gomail.localhost:{sandbox_port}/?run={run_id}",
                "Inspect the rendered confirmation email.",
            ),
            (
                ToolName.OPEN_URL,
                None,
                f"http://northstar.localhost:{sandbox_port}/?run={run_id}",
                "Corroborate the booking in rendered Manage Trip.",
            ),
            (
                ToolName.OPEN_URL,
                None,
                f"http://dayplan.localhost:{sandbox_port}/?run={run_id}",
                "Open the rendered calendar after booking verification.",
            ),
            (ToolName.CLICK, (874, 338), None, "Synchronize the verified travel block."),
            (ToolName.FINISH, None, None, "Finish only after visible final verification."),
        ]

    async def decide(self, *, observation: Observation, context: dict[str, Any]) -> ActionProposal:
        tool, coordinates, text, summary = self.actions[self.calls]
        self.calls += 1
        return ActionProposal(
            run_id=self.run_id,
            step_number=self.calls,
            plan_version=1,
            observation_id=observation.observation_id,
            observation_hash=observation.screenshot_hash,
            tool=tool,
            target_description=summary,
            coordinates_normalized=coordinates,
            text=text,
            grounding_confidence=Decimal("1"),
            decision_summary=summary,
        )


def browser_contract(*, port: int) -> TaskContract:
    payload = reference_task_contract().model_dump(mode="json", exclude={"content_hash"})
    payload["allowed_origins"] = [
        f"http://gomail.localhost:{port}",
        f"http://northstar.localhost:{port}",
        f"http://dayplan.localhost:{port}",
    ]
    payload["max_steps"] = 12
    return TaskContract.model_validate(payload)


@pytest.mark.asyncio
async def test_rendered_mock_workflow_enforces_approval_and_finishes_once() -> None:
    port = int(os.getenv("SANDBOX_E2E_PORT", "3101"))
    run_id = uuid7()
    contract = browser_contract(port=port)
    clock = FrozenSecurityClock(datetime(2026, 7, 10, tzinfo=UTC))
    authority = ApprovalAuthority(
        signing_key=b"browser-e2e-runtime-approval-key-32-bytes",
        clock=clock,
        default_ttl_seconds=180,
    )
    channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL") or None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=channel, headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="America/Los_Angeles",
            color_scheme="light",
        )
        reset = await context.request.post(
            f"http://localhost:{port}/api/sandbox/reset",
            data={"runId": str(run_id), "scenarioSeed": 1001, "faultId": "NONE"},
            headers={"x-sandbox-admin-token": _SANDBOX_ADMIN_TOKEN},
        )
        assert reset.ok
        page = await context.new_page()
        await page.goto(f"http://gomail.localhost:{port}/?run={run_id}")
        await page.wait_for_timeout(500)
        browser_page = cast(BrowserPage, page)

        async def approve(_request) -> bool:
            return True

        async def bind(effect: EffectProposal) -> None:
            assert isinstance(effect.context, BookingCommitContext)
            await bind_sandbox_approval(
                page=cast(PageWithContext, page),
                sandbox_origin=f"http://northstar.localhost:{port}",
                admin_token=_SANDBOX_ADMIN_TOKEN,
                run_id=str(run_id),
                context=effect.context,
            )

        adapter = CoordinateMockAdapter(run_id=run_id, sandbox_port=port)
        events = MemoryEventSink()
        orchestrator = AgentOrchestrator(
            machine=RunStateMachine(run_id=run_id, clock=clock),
            contract=contract,
            page=browser_page,
            observer=ScreenshotObserver(observation_id_factory=uuid7),
            adapter=adapter,
            target_resolver=SandboxTargetResolver(),
            executor=BrowserExecutor(allowed_origins=set(contract.allowed_origins)),
            verifier=VisibleTextVerifier(),
            policy=DeterministicPolicyEngine(clock),
            approval_broker=InMemoryApprovalBroker(
                authority=authority,
                decide=approve,
                bind=bind,
            ),
            authority=authority,
            events=events,
            context=DecisionContextAssembler(
                contract=contract,
                plan={"plan_version": 1, "goal": contract.goal, "subgoals": []},
            ),
        )
        try:
            final_state = await orchestrator.run()
        except Exception:
            print(events.events)
            print(await page.evaluate("() => document.body.innerText"))
            raise
        if final_state is not RunState.SUCCEEDED:
            print(events.events)
            print(await page.evaluate("() => document.body.innerText"))
        assert final_state is RunState.SUCCEEDED
        assert adapter.calls == len(adapter.actions)
        public_state = await context.request.get(
            f"http://localhost:{port}/api/sandbox/state?runId={run_id}"
        )
        state = (await public_state.json())["state"]
        assert state["booking"]["status"] == "confirmed"
        assert state["booking"]["flight"]["flightId"] == "NS451"
        assert state["calendar"]["updateCount"] == 1
        assert any(
            kind == "policy.decision" and payload.get("verdict") == "REQUIRE_APPROVAL"
            for kind, payload in events.events
        )
        await browser.close()


@pytest.mark.asyncio
async def test_real_browser_context_denies_runtime_oracle_and_external_egress() -> None:
    port = int(os.getenv("SANDBOX_E2E_PORT", "3101"))
    allowed = {
        f"http://gomail.localhost:{port}",
        f"http://northstar.localhost:{port}",
        f"http://dayplan.localhost:{port}",
    }
    channel = os.getenv("PLAYWRIGHT_BROWSER_CHANNEL") or None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=channel, headless=True)
        context = await browser.new_context()
        await install_browser_egress_guard(cast(Any, context), allowed_origins=allowed)
        page = await context.new_page()
        await page.goto(f"http://gomail.localhost:{port}/")

        for forbidden in (
            "http://localhost:8000/v1/evaluations",
            "http://oracle.localhost:9000/api/sandbox/state?view=oracle",
            "https://example.com/",
        ):
            with pytest.raises(PlaywrightError):
                await page.goto(forbidden, wait_until="domcontentloaded")
            assert page.url != forbidden
        await page.goto(f"http://gomail.localhost:{port}/")
        assert page.url.startswith(f"http://gomail.localhost:{port}/")
        await browser.close()
