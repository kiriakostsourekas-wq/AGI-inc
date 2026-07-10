"""Trusted sandbox metadata resolution and server-only approval binding."""

from __future__ import annotations

from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    CalendarMutationContext,
    ReadEffectContext,
    ToolName,
    TrustedTargetKind,
    jcs_canonicalize,
    normalize_origin,
)

from .browser import BrowserPage
from .effects import TrustedTargetDescriptor
from .sandbox_context import sandbox_approval_context_hash


class ApiResponse(Protocol):
    @property
    def ok(self) -> bool: ...

    @property
    def status(self) -> int: ...

    async def json(self) -> Any: ...


class ApiRequestContext(Protocol):
    async def post(
        self, url: str, *, data: dict[str, object], headers: dict[str, str]
    ) -> ApiResponse: ...


class BrowserContextWithRequest(Protocol):
    @property
    def request(self) -> ApiRequestContext: ...


class PageWithContext(BrowserPage, Protocol):
    @property
    def context(self) -> BrowserContextWithRequest: ...


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return normalize_origin(f"{parsed.scheme}://{parsed.netloc}")


class SandboxTargetResolver:
    """Resolve rendered coordinates to registered runtime-only semantic metadata."""

    async def resolve(
        self, *, page: BrowserPage, action: ActionProposal
    ) -> TrustedTargetDescriptor | None:
        if action.tool in {ToolName.FINISH, ToolName.SAFE_ABORT}:
            return None
        if action.tool is ToolName.OPEN_URL:
            if action.text is None:
                return None
            origin = _origin(action.text)
            return TrustedTargetDescriptor(
                target_kind=TrustedTargetKind.NAVIGATION,
                origin=origin,
                trusted_target_id=f"navigation:{origin}",
                context=ReadEffectContext(resource_type="origin", resource_id=origin),
            )
        if action.tool in {ToolName.KEYPRESS, ToolName.SCROLL, ToolName.BACK, ToolName.WAIT}:
            origin = _origin(page.url)
            return TrustedTargetDescriptor(
                target_kind=TrustedTargetKind.READ_ONLY_CONTROL,
                origin=origin,
                trusted_target_id=f"browser:{action.tool.value}",
                context=ReadEffectContext(
                    resource_type="browser_control", resource_id=action.tool.value
                ),
            )
        if action.coordinates_normalized is None:
            return None
        x, y = action.coordinates_normalized
        metadata = await page.evaluate(
            """([x, y]) => {
              const element = document.elementFromPoint(x * 1.44, y * 0.9);
              const control = element?.closest('[data-trust-target]');
              if (!control) return null;
              let context = null;
              const raw = control.getAttribute('data-trust-context');
              if (raw) { try { context = JSON.parse(raw); } catch { return null; } }
              return { target: control.getAttribute('data-trust-target'), context };
            }""",
            [x, y],
        )
        if not isinstance(metadata, dict):
            return None
        trusted = cast(dict[str, object], metadata)
        target = trusted.get("target")
        if not isinstance(target, str):
            return None
        origin = _origin(page.url)
        raw_context = trusted.get("context")
        if target == "northstar.confirm-rebooking" and isinstance(raw_context, dict):
            context = BookingCommitContext.model_validate(raw_context)
            kind = TrustedTargetKind.BOOKING_CONFIRM
        elif target == "dayplan.save-calendar" and isinstance(raw_context, dict):
            context = CalendarMutationContext.model_validate(raw_context)
            kind = TrustedTargetKind.CALENDAR_SAVE
        else:
            context = ReadEffectContext(resource_type="rendered_control", resource_id=target)
            kind = TrustedTargetKind.READ_ONLY_CONTROL
        return TrustedTargetDescriptor(
            target_kind=kind,
            origin=origin,
            trusted_target_id=target,
            context=context,
        )


async def bind_sandbox_approval(
    *,
    page: PageWithContext,
    sandbox_origin: str,
    admin_token: str,
    run_id: str,
    context: BookingCommitContext,
    runtime_grant_id: str | None = None,
) -> dict[str, object]:
    """Bind an approved semantic effect to the isolated browser's HttpOnly cookie."""

    if not admin_token:
        raise ValueError("sandbox admin token is required")
    expected_hash = sandbox_approval_context_hash(run_id=run_id, context=context)
    request_body: dict[str, object] = {
        "runId": run_id,
        "flightId": context.flight_id,
        "expectedContextHash": expected_hash,
    }
    if runtime_grant_id is not None:
        request_body["runtimeGrantId"] = runtime_grant_id
    response = await page.context.request.post(
        f"{normalize_origin(sandbox_origin)}/api/sandbox/approval",
        data=request_body,
        headers={"x-sandbox-admin-token": admin_token},
    )
    response_body: object = await response.json()
    if not response.ok:
        raise RuntimeError(f"sandbox approval binding failed with HTTP {response.status}")
    if not isinstance(response_body, dict):
        raise RuntimeError("sandbox approval binding returned malformed JSON")
    result = cast(dict[str, object], response_body)
    approval = result.get("approval")
    if not isinstance(approval, dict):
        raise RuntimeError("sandbox approval binding response omitted approval metadata")
    approval_payload = cast(dict[str, object], approval)
    if approval_payload.get("contextHash") != expected_hash:
        raise RuntimeError("sandbox approval binding context does not match runtime effect")
    return result


def canonical_context_bytes(*, run_id: str, context: BookingCommitContext) -> bytes:
    """Exposed only for cross-language gateway tests."""

    return jcs_canonicalize(
        {
            "runId": run_id,
            "contextHash": sandbox_approval_context_hash(run_id=run_id, context=context),
        }
    )
