"""Exact integer-minor-unit money values with stable JSON serialization."""
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CENT = Decimal("0.01")


class Money(BaseModel):
    """An exact amount encoded as integer minor units and ISO-4217 currency.

    ``amount`` is accepted as a construction-only compatibility input so callers
    cannot accidentally introduce binary floats. The canonical/wire shape is
    always ``{"amount_minor": 45000, "currency": "USD"}``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    amount_minor: int = Field(ge=0, le=(1 << 53) - 1)
    currency: str

    @model_validator(mode="before")
    @classmethod
    def accept_exact_decimal_input(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "amount_minor" in value or "amount" not in value:
            return value
        raw = value["amount"]
        if isinstance(raw, float):
            raise ValueError("money must not be constructed from a float")
        try:
            parsed = raw if isinstance(raw, Decimal) else Decimal(str(raw))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("money amount must be a finite decimal") from error
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("money amount must be a finite non-negative decimal")
        exponent = parsed.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise ValueError("money amount may have at most two fractional digits")
        converted = dict(value)
        converted.pop("amount", None)
        converted["amount_minor"] = int(parsed.quantize(_CENT) * 100)
        return converted

    @field_validator("amount_minor", mode="before")
    @classmethod
    def reject_non_integer_minor_units(cls, value: Any) -> int:
        if isinstance(value, float):
            raise ValueError("money minor units must be an integer")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("money minor units must be an integer")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = value.upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ASCII code")
        return normalized

    @property
    def amount(self) -> Decimal:
        """Exact major-unit view for local arithmetic; never serialized."""

        return (Decimal(self.amount_minor) / 100).quantize(_CENT)

    @property
    def minor_units(self) -> int:
        return self.amount_minor

    def ensure_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError("currency mismatch")

    def __le__(self, other: "Money") -> bool:
        self.ensure_same_currency(other)
        return self.amount_minor <= other.amount_minor
