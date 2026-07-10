"""Stable semantic and request idempotency primitives."""

from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import UUID

from trust_contracts import EffectClass, sha256_hex

from .errors import IdempotencyConflictError


def derive_effect_idempotency_key(
    *,
    run_id: UUID,
    resource_type: str,
    resource_id: str,
    effect_class: EffectClass,
    approved_context_hash: str,
) -> str:
    """Derive a collision-resistant key from structured semantic fields."""

    return sha256_hex(
        {
            "version": 1,
            "run_id": str(run_id),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "effect_class": effect_class.value,
            "approved_context_hash": approved_context_hash,
        }
    )


def replacement_booking_resource_key(original_reservation_id: str) -> str:
    """Business uniqueness key: one replacement per original reservation."""

    return sha256_hex(
        {
            "version": 1,
            "resource_type": "replacement_booking",
            "original_reservation_id": original_reservation_id,
        }
    )


def request_fingerprint(*, method: str, path: str, body: Any) -> str:
    return sha256_hex({"method": method.upper(), "path": path, "body": body})


@dataclass(frozen=True, slots=True)
class StoredIdempotentResponse:
    fingerprint: str
    status_code: int
    body: dict[str, Any]


class InMemoryIdempotencyStore:
    """Thread-safe API stub store; PostgreSQL replaces this in the durable runtime."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], StoredIdempotentResponse] = {}
        self._lock = RLock()

    def lookup(
        self, *, namespace: str, key: str, fingerprint: str
    ) -> StoredIdempotentResponse | None:
        self._validate_key(key)
        with self._lock:
            record = self._records.get((namespace, key))
            if record is not None and record.fingerprint != fingerprint:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different request body"
                )
            return record

    def save(
        self,
        *,
        namespace: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> StoredIdempotentResponse:
        self._validate_key(key)
        candidate = StoredIdempotentResponse(fingerprint, status_code, body)
        with self._lock:
            existing = self._records.get((namespace, key))
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different request body"
                    )
                return existing
            self._records[(namespace, key)] = candidate
            return candidate

    @staticmethod
    def _validate_key(key: str) -> None:
        if not 8 <= len(key) <= 128 or any(character.isspace() for character in key):
            raise ValueError("idempotency key must be 8..128 non-whitespace characters")
