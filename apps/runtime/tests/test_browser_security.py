from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from trust_contracts import ActionProposal, ReadEffectContext, ToolName, TrustedTargetKind, uuid7

from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.browser import (
    BrowserExecutor,
    BrowserPage,
    BrowserRoute,
    install_browser_egress_guard,
)
from trust_runtime.effects import EffectDeriver, TrustedTargetDescriptor
from trust_runtime.policy import DeterministicPolicyEngine, PolicyContext


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.aborted = False
        self.continued = False

    async def abort(self, _error_code: str = "blockedbyclient") -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


class FakeContext:
    def __init__(self) -> None:
        self.handler: Any = None

    async def route(self, _url: str, handler: Any) -> None:
        self.handler = handler


class NavigationPage:
    def __init__(self, *, initial_url: str, navigation_result: str | None = None) -> None:
        self.url = initial_url
        self.navigation_result = navigation_result
        self.mouse = self
        self.keyboard = self

    async def screenshot(self, *, type: str = "png") -> bytes:
        return b"rendered-png"

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.url = self.navigation_result or url

    async def go_back(self, *, wait_until: str = "domcontentloaded") -> None:
        return None

    async def wait_for_timeout(self, _timeout: float) -> None:
        return None

    async def click(self, _x: float, _y: float) -> None:
        if self.navigation_result is not None:
            self.url = self.navigation_result

    async def dblclick(self, _x: float, _y: float) -> None:
        await self.click(_x, _y)

    async def wheel(self, _x: float, _y: float) -> None:
        return None

    async def type(self, _text: str) -> None:
        return None

    async def press(self, _text: str) -> None:
        return None

    async def evaluate(self, _expression: str, _arg: Any = None) -> None:
        return None


def authorized_read_action(*, contract, clock, tool: ToolName, text: str | None = None):
    action = ActionProposal(
        run_id=uuid7(),
        step_number=1,
        plan_version=1,
        observation_id=uuid7(),
        observation_hash="a" * 64,
        tool=tool,
        target_description="Test an exact browser boundary.",
        coordinates_normalized=(500, 500) if tool is ToolName.CLICK else None,
        text=text,
        grounding_confidence=Decimal("1"),
        decision_summary="Exercise the browser origin boundary.",
    )
    origin = contract.allowed_origins[0]
    target = TrustedTargetDescriptor(
        target_kind=(
            TrustedTargetKind.NAVIGATION
            if tool is ToolName.OPEN_URL
            else TrustedTargetKind.READ_ONLY_CONTROL
        ),
        origin=origin,
        trusted_target_id="test.navigation",
        context=ReadEffectContext(resource_type="origin", resource_id=origin),
    )
    effect = EffectDeriver().derive(
        action=action,
        contract=contract,
        trusted_target=target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract=contract))
    authority = ApprovalAuthority(
        signing_key=b"browser-boundary-test-key-32-bytes",
        clock=clock,
        default_ttl_seconds=180,
    )
    return authority.authorize_allowed(effect=effect, policy_decision=decision)


@pytest.mark.asyncio
async def test_context_egress_guard_blocks_oracle_api_and_unknown_protocols(contract) -> None:
    context = FakeContext()
    await install_browser_egress_guard(
        context,
        allowed_origins=set(contract.allowed_origins),
    )
    assert context.handler is not None

    cases = [
        (f"{contract.allowed_origins[0]}/gomail", False),
        (contract.allowed_origins[0].replace("http://", "ws://") + "/events", False),
        ("http://localhost:8000/v1/evaluations", True),
        ("http://oracle.internal:9000/api/sandbox/state?view=oracle", True),
        ("https://example.com/redirect", True),
        ("file:///etc/passwd", True),
        ("data:image/png;base64,AA==", False),
    ]
    for url, should_abort in cases:
        route = FakeRoute(url)
        await context.handler(cast(BrowserRoute, route))
        assert route.aborted is should_abort
        assert route.continued is not should_abort


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [ToolName.OPEN_URL, ToolName.CLICK])
async def test_executor_fails_closed_after_redirect_or_click_escape(contract, clock, tool) -> None:
    allowed = contract.allowed_origins[0]
    action = authorized_read_action(
        contract=contract,
        clock=clock,
        tool=tool,
        text=f"{allowed}/gomail" if tool is ToolName.OPEN_URL else None,
    )
    page = NavigationPage(initial_url=allowed, navigation_result="https://outside.example/phish")

    with pytest.raises(PermissionError, match="escaped"):
        await BrowserExecutor(allowed_origins=set(contract.allowed_origins)).execute(
            cast(BrowserPage, page), action
        )


@pytest.mark.asyncio
async def test_executor_keeps_allowlisted_navigation(contract, clock) -> None:
    target = f"{contract.allowed_origins[0]}/gomail"
    action = authorized_read_action(
        contract=contract,
        clock=clock,
        tool=ToolName.OPEN_URL,
        text=target,
    )
    page = NavigationPage(initial_url=contract.allowed_origins[0])
    receipt = await BrowserExecutor(allowed_origins=set(contract.allowed_origins)).execute(
        cast(BrowserPage, page), action
    )
    assert receipt.url_after == target
