"""Normalize identifiers so `apply_late_fee` and `applyLateFee` share terms."""

from __future__ import annotations

import re

_CAMEL_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NON_WORD = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")


def soften_identifiers(text: str) -> str:
    text = _CAMEL_1.sub(r"\1 \2", text)
    text = _CAMEL_2.sub(r"\1 \2", text)
    return text


def normalize_text(text: str) -> str:
    softened = soften_identifiers(text).lower()
    softened = softened.replace("_", " ").replace("-", " ")
    softened = softened.replace("/", " ").replace(".", " ").replace(":", " ")
    softened = _NON_WORD.sub(" ", softened)
    return _SPACE.sub(" ", softened).strip()
