"""Stderr progress. Stdout stays reserved for decks and JSON."""

from __future__ import annotations

import sys


def emit(message: str) -> None:
    print(f"skimsearch: {message}", file=sys.stderr)
