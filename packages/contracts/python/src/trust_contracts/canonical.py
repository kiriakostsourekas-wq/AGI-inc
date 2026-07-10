# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
"""A strict RFC 8785-compatible JSON canonicalization profile.

The runtime deliberately rejects binary floating-point values. Contract money is
serialized as a fixed decimal string and confidence values are also strings in JSON
mode, leaving canonical JSON with strings, booleans, nulls, arrays, objects, and
safe integers. This keeps hashes identical across Python and TypeScript.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

MAX_SAFE_INTEGER = (1 << 53) - 1


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by the strict JCS profile."""


def _validate_unicode(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CanonicalizationError("lone Unicode surrogate is not valid JCS text")


def _string(value: str) -> str:
    _validate_unicode(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _key_sort(value: str) -> bytes:
    _validate_unicode(value)
    return value.encode("utf-16-be")


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if isinstance(value, Enum):
        return _normalise(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError("canonical timestamps must be timezone-aware")
        return value.isoformat()
    if isinstance(value, Decimal):
        raise CanonicalizationError("raw Decimal is forbidden; serialize through Money")
    if isinstance(value, float):
        raise CanonicalizationError("binary floating-point values are forbidden")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            output[key] = _normalise(item)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalise(item) for item in value]
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer exceeds the cross-language safe range")
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        ordered_keys = sorted(value, key=_key_sort)
        members = (_string(key) + ":" + _encode(value[key]) for key in ordered_keys)
        return "{" + ",".join(members) + "}"
    raise CanonicalizationError(f"normalization produced unsupported value: {type(value).__name__}")


def jcs_canonicalize(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes for a supported value."""

    return _encode(_normalise(value)).encode("utf-8")


def sha256_hex(value: Any) -> str:
    """Hash a value after canonicalization."""

    return hashlib.sha256(jcs_canonicalize(value)).hexdigest()
