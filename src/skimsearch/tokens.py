"""Cheap token estimates. Coding agents bill roughly four characters per token."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Return a stable lower-bound-ish token count for budgeting snippets."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
