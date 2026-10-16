"""Environment and .env loading.

The default provider is AtmoRouter (https://atmorouter.dev), an OpenAI-compatible
gateway. Set SKIMSEARCH_BASE_URL and a key to point at any other compatible API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from skimsearch.errors import SkimError

DEFAULT_BASE_URL = "https://atmorouter.dev/v1"
DEFAULT_MODEL = "atmo/deepseek-v4.1-flash"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_BUDGET = 700
DEFAULT_CARD_TOKENS = 140
DEFAULT_TIMEOUT = 60.0
DEFAULT_API_STYLE = "openai"

_KEY_NAMES = ("ATMOROUTER_API_KEY", "SKIMSEARCH_API_KEY", "OPENAI_API_KEY")
_BASE_NAMES = ("SKIMSEARCH_BASE_URL", "OPENAI_BASE_URL")


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    key_source: str | None
    base_url: str
    model: str
    embed_model: str | None
    api_style: str
    budget: int
    card_tokens: int
    timeout: float


def load_dotenv_file(path: Path, into: dict[str, str], *, override: bool = False) -> None:
    """Merge KEY=VALUE lines from a dotenv file. Existing keys win unless override."""
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            continue
        if override or key not in into or into.get(key, "") == "":
            into[key] = value


def load_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_paths: list[Path] | None = None,
) -> Settings:
    """Build settings from the process environment and optional dotenv files.

    When `environ` is omitted, the real process environment is copied and
    dotenv files are applied without overriding variables that are already set.
    When `environ` is passed, only that mapping and `dotenv_paths` are used.
    """
    if environ is None:
        env = dict(os.environ)
        paths = list(dotenv_paths) if dotenv_paths is not None else [Path.cwd() / ".env"]
    else:
        env = {str(k): str(v) for k, v in environ.items()}
        paths = list(dotenv_paths or [])
    for path in paths:
        load_dotenv_file(path, env, override=False)

    key_source = next((name for name in _KEY_NAMES if env.get(name, "").strip()), None)
    api_key = env.get(key_source, "").strip() if key_source else None
    base = ""
    for name in _BASE_NAMES:
        if env.get(name, "").strip():
            base = env[name].strip()
            break
    base_url = (base or DEFAULT_BASE_URL).rstrip("/")
    model = env.get("SKIMSEARCH_MODEL", "").strip() or DEFAULT_MODEL
    embed_raw = env.get("SKIMSEARCH_EMBED_MODEL", "").strip()
    api_style = env.get("SKIMSEARCH_API_STYLE", "").strip().lower() or DEFAULT_API_STYLE
    if api_style not in {"openai", "anthropic"}:
        raise SkimError("SKIMSEARCH_API_STYLE must be 'openai' or 'anthropic'")
    budget = _positive_int(env.get("SKIMSEARCH_BUDGET", ""), DEFAULT_BUDGET, "SKIMSEARCH_BUDGET")
    card_tokens = _positive_int(
        env.get("SKIMSEARCH_CARD_TOKENS", ""),
        DEFAULT_CARD_TOKENS,
        "SKIMSEARCH_CARD_TOKENS",
    )
    timeout = _positive_float(
        env.get("SKIMSEARCH_TIMEOUT", ""),
        DEFAULT_TIMEOUT,
        "SKIMSEARCH_TIMEOUT",
    )
    return Settings(
        api_key=api_key or None,
        key_source=key_source,
        base_url=base_url,
        model=model,
        embed_model=embed_raw or None,
        api_style=api_style,
        budget=budget,
        card_tokens=card_tokens,
        timeout=timeout,
    )


def mask_secret(value: str | None) -> str:
    if not value:
        return "(missing)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}…{value[-2:]}"


def _positive_int(raw: str, default: int, name: str) -> int:
    if not raw.strip():
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise SkimError(f"{name} must be an integer") from exc
    if number < 1:
        raise SkimError(f"{name} must be positive")
    return number


def _positive_float(raw: str, default: float, name: str) -> float:
    if not raw.strip():
        return default
    try:
        number = float(raw)
    except ValueError as exc:
        raise SkimError(f"{name} must be a number") from exc
    if number <= 0:
        raise SkimError(f"{name} must be positive")
    return number
