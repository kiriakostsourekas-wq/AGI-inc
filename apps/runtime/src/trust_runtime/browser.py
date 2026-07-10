"""Screenshot-only browser adapter and provider boundary.

The actor receives a PNG and trusted URL metadata only. DOM inspection is kept in
``resolve_trusted_target`` and is called by the runtime policy layer, never by the
model adapter. This makes the visibility boundary explicit and testable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from trust_contracts import ActionProposal, AuthorizedAction, ToolName, normalize_origin

from .telemetry import set_attributes, tracer


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    url: str
    origin: str
    screenshot_png: bytes
    screenshot_hash: str
    viewport: tuple[int, int] = (1440, 900)


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    action_id: str
    tool: str
    url_after: str
    screenshot_hash_after: str


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class BrowserPage(Protocol):
    url: str

    async def screenshot(self, *, type: str = "png") -> bytes: ...

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> Any: ...

    async def go_back(self, *, wait_until: str = "domcontentloaded") -> Any: ...

    async def wait_for_timeout(self, timeout: float) -> None:  # noqa: ASYNC109
        ...

    @property
    def mouse(self) -> Any: ...

    @property
    def keyboard(self) -> Any: ...

    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...


class BrowserRoute(Protocol):
    @property
    def request(self) -> Any: ...

    async def abort(self, error_code: str = "blockedbyclient") -> None: ...

    async def continue_(self) -> None: ...


class RoutableBrowserContext(Protocol):
    async def route(
        self,
        url: str,
        handler: Callable[[BrowserRoute], Awaitable[None]],
    ) -> None: ...


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("browser URL must include a scheme and host")
    return normalize_origin(f"{parsed.scheme}://{parsed.netloc}")


def _network_origin(url: str) -> str | None:
    parsed = urlsplit(url)
    try:
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return normalize_origin(f"{parsed.scheme}://{parsed.netloc}")
        if parsed.scheme in {"ws", "wss"} and parsed.hostname:
            http_scheme = "http" if parsed.scheme == "ws" else "https"
            return normalize_origin(f"{http_scheme}://{parsed.netloc}")
    except ValueError:
        return None
    return None


async def install_browser_egress_guard(
    context: RoutableBrowserContext,
    *,
    allowed_origins: set[str],
) -> None:
    """Abort every browser HTTP(S)/WebSocket request outside exact sandbox origins."""

    normalized = {normalize_origin(origin) for origin in allowed_origins}

    async def enforce(route: BrowserRoute) -> None:
        url_value = getattr(route.request, "url", None)
        if not isinstance(url_value, str):
            await route.abort("blockedbyclient")
            return
        parsed = urlsplit(url_value)
        origin = _network_origin(url_value)
        if parsed.scheme in {"http", "https", "ws", "wss"} and origin not in normalized:
            await route.abort("blockedbyclient")
            return
        if parsed.scheme not in {"http", "https", "ws", "wss", "about", "blob", "data"}:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    await context.route("**/*", enforce)


class ScreenshotObserver:
    def __init__(self, *, observation_id_factory: Any) -> None:
        self._observation_id_factory = observation_id_factory

    async def capture(self, page: BrowserPage) -> Observation:
        screenshot = await page.screenshot(type="png")
        return Observation(
            observation_id=str(self._observation_id_factory()),
            url=page.url,
            origin=_origin(page.url),
            screenshot_png=screenshot,
            screenshot_hash=hashlib.sha256(screenshot).hexdigest(),
        )


class BrowserExecutor:
    """Executes only an already-authorized action envelope."""

    def __init__(self, *, allowed_origins: set[str]) -> None:
        self._allowed_origins = {normalize_origin(origin) for origin in allowed_origins}

    async def execute(self, page: BrowserPage, action: AuthorizedAction) -> ActionReceipt:
        proposal = action.action
        with tracer("browser").start_as_current_span("browser.action") as span:
            set_attributes(
                span,
                {
                    "trust.run_id": str(proposal.run_id),
                    "trust.action_id": str(proposal.action_id),
                    "trust.step_number": proposal.step_number,
                    "trust.tool": proposal.tool.value,
                },
            )
            return await self._execute_impl(page, action)

    async def _execute_impl(self, page: BrowserPage, action: AuthorizedAction) -> ActionReceipt:
        proposal = action.action
        if proposal.tool is ToolName.OPEN_URL:
            if proposal.text is None:
                raise ValueError("ui.open_url requires a URL")
            target_origin = _origin(proposal.text)
            if target_origin not in self._allowed_origins:
                raise PermissionError("navigation target is outside the exact origin allowlist")
            await page.goto(proposal.text, wait_until="domcontentloaded")
        elif proposal.tool in {ToolName.CLICK, ToolName.DOUBLE_CLICK, ToolName.TYPE_TEXT}:
            if proposal.coordinates_normalized is None:
                raise ValueError("coordinate action is missing normalized coordinates")
            x, y = proposal.coordinates_normalized
            px, py = x * 1440 / 1000, y * 900 / 1000
            if proposal.tool is ToolName.CLICK:
                await page.mouse.click(px, py)
            elif proposal.tool is ToolName.DOUBLE_CLICK:
                await page.mouse.dblclick(px, py)
            else:
                if proposal.text is None:
                    raise ValueError("ui.type_text requires text")
                await page.mouse.click(px, py)
                await page.keyboard.type(proposal.text)
        elif proposal.tool is ToolName.KEYPRESS:
            await page.keyboard.press(proposal.text or "")
        elif proposal.tool is ToolName.SCROLL:
            direction = proposal.text or "down"
            dy = 700 if direction == "down" else -700
            await page.mouse.wheel(0, dy)
        elif proposal.tool is ToolName.BACK:
            await page.go_back(wait_until="domcontentloaded")
        elif proposal.tool is ToolName.WAIT:
            seconds = float(proposal.text or "1")
            if not 0 <= seconds <= 30:
                raise ValueError("ui.wait is limited to 30 seconds")
            await page.wait_for_timeout(seconds * 1000)
        elif proposal.tool in {ToolName.FINISH, ToolName.SAFE_ABORT}:
            raise ValueError("runtime terminal actions are handled by the orchestrator")
        else:
            raise ValueError(f"unsupported browser action: {proposal.tool}")

        await page.wait_for_timeout(250)
        if _origin(page.url) not in self._allowed_origins:
            raise PermissionError("browser navigation escaped the exact origin allowlist")
        screenshot = await page.screenshot(type="png")
        return ActionReceipt(
            action_id=str(proposal.action_id),
            tool=proposal.tool.value,
            url_after=page.url,
            screenshot_hash_after=hashlib.sha256(screenshot).hexdigest(),
        )


async def resolve_trusted_target(page: BrowserPage, x: int, y: int) -> str | None:
    """Return only a registered target identifier at a rendered coordinate.

    The identifier is resolved by the runtime after the actor proposes a point;
    no DOM content is included in the actor prompt or action proposal.
    """

    px, py = x * 1440 / 1000, y * 900 / 1000
    value = await page.evaluate(
        """([x, y]) => {
          const element = document.elementFromPoint(x, y);
          return element?.closest('[data-trust-target]')?.getAttribute('data-trust-target') ?? null;
        }""",
        [px, py],
    )
    return value if isinstance(value, str) else None


class AgentAdapter(Protocol):
    async def decide(
        self, *, observation: Observation, context: dict[str, Any]
    ) -> ActionProposal: ...


@runtime_checkable
class UsageReportingAdapter(Protocol):
    def take_usage(self) -> ModelUsage: ...


class MockAgentAdapter:
    """Deterministic test adapter; never reports itself as a live run."""

    async def decide(self, *, observation: Observation, context: dict[str, Any]) -> ActionProposal:
        raw = context.get("proposal")
        if not isinstance(raw, dict):
            raise ValueError("mock adapter requires an explicit fixture proposal")
        return ActionProposal.model_validate(raw)

    def take_usage(self) -> ModelUsage:
        return ModelUsage(input_tokens=0, output_tokens=0, cost_usd=Decimal("0"))


class OpenAIResponsesAdapter:
    """Provider-agnostic boundary for the pinned OpenAI vision adapter."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        schema: dict[str, Any],
        input_cost_per_million_usd: Decimal,
        output_cost_per_million_usd: Decimal,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: int,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for live adapter")
        self.model = model
        self._schema = schema
        self._input_cost = input_cost_per_million_usd
        self._output_cost = output_cost_per_million_usd
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._usage: ModelUsage | None = None
        try:
            from openai import AsyncOpenAI
        except ImportError as error:  # pragma: no cover - exercised in deployment
            raise RuntimeError("install the runtime openai dependency for live runs") from error
        self._client: Any = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

    async def decide(self, *, observation: Observation, context: dict[str, Any]) -> ActionProposal:
        image = base64.b64encode(observation.screenshot_png).decode("ascii")
        prompt = json.dumps({"context": context, "observation_id": observation.observation_id})
        with tracer("model").start_as_current_span("gen_ai.response") as span:
            set_attributes(
                span,
                {
                    "gen_ai.provider.name": "openai",
                    "gen_ai.request.model": self.model,
                    "trust.observation_id": observation.observation_id,
                },
            )
            response: Any = await self._client.responses.create(
                model=self.model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": f"data:image/png;base64,{image}",
                            },
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "action_proposal",
                        "schema": self._schema,
                        "strict": True,
                    }
                },
            )
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            raise RuntimeError("model response omitted billable token usage")
        cost = (
            Decimal(input_tokens) * self._input_cost + Decimal(output_tokens) * self._output_cost
        ) / Decimal(1_000_000)
        self._usage = ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        return ActionProposal.model_validate_json(response.output_text)

    def take_usage(self) -> ModelUsage:
        usage = self._usage
        if usage is None:
            raise RuntimeError("model usage was requested before a completed response")
        self._usage = None
        return usage
