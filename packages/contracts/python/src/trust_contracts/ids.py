"""Identifier helpers shared by runtime components."""

import secrets
import time
from uuid import UUID


def uuid7(*, timestamp_ms: int | None = None, random_bits: int | None = None) -> UUID:
    """Create an RFC 9562 UUIDv7 without relying on a database extension.

    ``timestamp_ms`` and ``random_bits`` are injectable solely for deterministic
    golden tests. Production callers should omit both.
    """

    current_ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if not 0 <= current_ms < 1 << 48:
        raise ValueError("timestamp_ms must fit in 48 bits")

    randomness = secrets.randbits(74) if random_bits is None else random_bits
    if not 0 <= randomness < 1 << 74:
        raise ValueError("random_bits must fit in 74 bits")

    rand_a = randomness >> 62
    rand_b = randomness & ((1 << 62) - 1)
    value = current_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return UUID(int=value)
