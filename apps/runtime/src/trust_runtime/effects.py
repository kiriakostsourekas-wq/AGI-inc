"""Runtime-owned effect derivation from actor proposals and trusted UI metadata."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    CalendarMutationContext,
    EffectClass,
    EffectContext,
    EffectProposal,
    ReadEffectContext,
    TaskContract,
    ToolName,
    TrustedTargetKind,
    normalize_origin,
    sha256_hex,
)

from .idempotency import derive_effect_idempotency_key

_TARGET_EFFECTS: dict[TrustedTargetKind, EffectClass] = {
    TrustedTargetKind.NAVIGATION: EffectClass.READ,
    TrustedTargetKind.READ_ONLY_CONTROL: EffectClass.READ,
    TrustedTargetKind.DRAFT_FIELD: EffectClass.DRAFT,
    TrustedTargetKind.BOOKING_CONFIRM: EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
    TrustedTargetKind.CALENDAR_SAVE: EffectClass.REVERSIBLE_MUTATION,
    TrustedTargetKind.EXTERNAL_SEND: EffectClass.EXTERNAL_COMMUNICATION,
    TrustedTargetKind.RUNTIME_FINISH: EffectClass.READ,
    TrustedTargetKind.RUNTIME_SAFE_ABORT: EffectClass.READ,
}


class TrustedTargetDescriptor(BaseModel):
    """Runtime-only semantic target resolved independently of the actor output."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    target_kind: TrustedTargetKind
    origin: str | None
    trusted_target_id: str
    context: EffectContext

    @field_validator("origin")
    @classmethod
    def origin_is_exact(cls, value: str | None) -> str | None:
        return None if value is None else normalize_origin(value)


class EffectDerivationError(ValueError):
    pass


class EffectDeriver:
    """Fail-closed derivation of semantic effects.

    The actor cannot select an effect class. Coordinate-to-target resolution is a
    separate trusted runtime concern and supplies ``TrustedTargetDescriptor``.
    """

    def derive(
        self,
        *,
        action: ActionProposal,
        contract: TaskContract,
        trusted_target: TrustedTargetDescriptor | None,
        derived_at: datetime,
    ) -> EffectProposal:
        if action.tool not in contract.allowed_tools:
            raise EffectDerivationError("actor proposed a tool outside the task contract")
        target = trusted_target or self._runtime_target(action)
        if target.origin is not None and target.origin not in contract.allowed_origins:
            raise EffectDerivationError("trusted browser origin is outside the task contract")
        self._validate_tool_target_pair(action.tool, target.target_kind)

        effect_class = _TARGET_EFFECTS[target.target_kind]
        context_hash = sha256_hex(
            {
                "contract_hash": contract.content_hash,
                "origin": target.origin,
                "effect_class": effect_class,
                "target_kind": target.target_kind,
                "context": target.context,
            }
        )
        idempotency_key: str | None = None
        if effect_class not in {EffectClass.READ, EffectClass.DRAFT}:
            resource_type, resource_id = self._resource_identity(target.context)
            idempotency_key = derive_effect_idempotency_key(
                run_id=action.run_id,
                resource_type=resource_type,
                resource_id=resource_id,
                effect_class=effect_class,
                approved_context_hash=context_hash,
            )

        return EffectProposal(
            action=action,
            contract_hash=contract.content_hash,
            origin=target.origin,
            effect_class=effect_class,
            trusted_target_kind=target.target_kind,
            context=target.context,
            approved_context_hash=context_hash,
            idempotency_key=idempotency_key,
            approval_required=effect_class
            in {
                EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
                EffectClass.CREDENTIAL_OR_IDENTITY,
            },
            derived_by_rule=f"trusted-target/{target.target_kind.value.lower()}/v1",
            derived_at=derived_at,
        )

    @staticmethod
    def _runtime_target(action: ActionProposal) -> TrustedTargetDescriptor:
        if action.tool is ToolName.FINISH:
            kind = TrustedTargetKind.RUNTIME_FINISH
        elif action.tool is ToolName.SAFE_ABORT:
            kind = TrustedTargetKind.RUNTIME_SAFE_ABORT
        else:
            raise EffectDerivationError("UI action requires a trusted target descriptor")
        return TrustedTargetDescriptor(
            target_kind=kind,
            origin=None,
            trusted_target_id=f"runtime:{kind.value.lower()}",
            context=ReadEffectContext(
                resource_type="runtime",
                resource_id=kind.value,
            ),
        )

    @staticmethod
    def _resource_identity(context: EffectContext) -> tuple[str, str]:
        if isinstance(context, BookingCommitContext):
            return "replacement_booking", context.reservation_id
        if isinstance(context, CalendarMutationContext):
            return "calendar_event", context.calendar_event_id
        return context.resource_type, context.resource_id

    @staticmethod
    def _validate_tool_target_pair(tool: ToolName, target_kind: TrustedTargetKind) -> None:
        click_targets = {
            TrustedTargetKind.BOOKING_CONFIRM,
            TrustedTargetKind.CALENDAR_SAVE,
            TrustedTargetKind.EXTERNAL_SEND,
        }
        if target_kind in click_targets and tool not in {ToolName.CLICK, ToolName.DOUBLE_CLICK}:
            raise EffectDerivationError("trusted control target requires a click action")
        if target_kind is TrustedTargetKind.READ_ONLY_CONTROL and tool not in {
            ToolName.CLICK,
            ToolName.DOUBLE_CLICK,
            ToolName.KEYPRESS,
            ToolName.SCROLL,
            ToolName.BACK,
            ToolName.WAIT,
        }:
            raise EffectDerivationError("read-only browser target has an incompatible tool")
        if target_kind is TrustedTargetKind.DRAFT_FIELD and tool is not ToolName.TYPE_TEXT:
            raise EffectDerivationError("draft target requires ui.type_text")
        if target_kind is TrustedTargetKind.NAVIGATION and tool is not ToolName.OPEN_URL:
            raise EffectDerivationError("navigation target requires ui.open_url")
        if target_kind is TrustedTargetKind.RUNTIME_FINISH and tool is not ToolName.FINISH:
            raise EffectDerivationError("finish target requires runtime.finish")
        if target_kind is TrustedTargetKind.RUNTIME_SAFE_ABORT and tool is not ToolName.SAFE_ABORT:
            raise EffectDerivationError("safe-abort target requires runtime.safe_abort")
