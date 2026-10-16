"""Little-endian float vectors stored beside skim cards."""

from __future__ import annotations

import struct


def pack_vector(values: list[float]) -> bytes:
    if not values:
        return b""
    return struct.pack("<" + "f" * len(values), *[float(value) for value in values])


def unpack_vector(blob: bytes) -> list[float]:
    if not blob or len(blob) % 4 != 0:
        return []
    count = len(blob) // 4
    return list(struct.unpack("<" + "f" * count, blob))


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for x_val, y_val in zip(left, right):
        dot += x_val * y_val
        left_norm += x_val * x_val
        right_norm += y_val * y_val
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / ((left_norm**0.5) * (right_norm**0.5))
