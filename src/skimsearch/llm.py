"""OpenAI-compatible and Anthropic-compatible chat calls.

Default base URL is AtmoRouter: https://atmorouter.dev/v1
Override the base URL and key to use any other compatible provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from skimsearch import __version__
from skimsearch.config import Settings
from skimsearch.errors import SkimError


class LLMError(SkimError):
    """The configured model endpoint rejected the call or returned an unusable body."""


@dataclass(frozen=True)
class LLMReply:
    text: str
    cost_usd: str | None = None


def join_url(base: str, suffix: str) -> str:
    return base.rstrip("/") + "/" + suffix.lstrip("/")


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")].strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("model did not return a JSON object")
    obj, _end = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(obj, dict):
        raise ValueError("model JSON was not an object")
    return obj


class LLMClient:
    def __init__(self, settings: Settings):
        if not settings.api_key:
            raise LLMError("no API key set; export ATMOROUTER_API_KEY")
        self.settings = settings

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int = 600) -> LLMReply:
        if self.settings.api_style == "anthropic":
            return self._anthropic(messages, max_tokens)
        return self._openai(messages, max_tokens)

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        payload = {"model": model, "input": texts}
        url = join_url(self.settings.base_url, "embeddings")
        body, _cost = post_json(
            url,
            _headers(self.settings, anthropic=False),
            payload,
            self.settings.timeout,
        )
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise LLMError("embedding response did not include vectors")
        ordered = sorted(
            (item for item in data if isinstance(item, dict)),
            key=lambda item: int(item.get("index", 0)),
        )
        vectors: list[list[float]] = []
        for item in ordered:
            raw = item.get("embedding")
            if not isinstance(raw, list) or not raw:
                raise LLMError("embedding response is missing a vector")
            vectors.append([float(value) for value in raw])
        if len(vectors) != len(texts):
            raise LLMError("embedding response size does not match the batch")
        return vectors

    def _openai(self, messages: list[dict[str, str]], max_tokens: int) -> LLMReply:
        url = join_url(self.settings.base_url, "chat/completions")
        payload: dict = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        body, cost = _post_with_temperature_retry(
            url,
            _headers(self.settings, anthropic=False),
            payload,
            self.settings.timeout,
        )
        return LLMReply(text=_openai_text(body), cost_usd=cost)

    def _anthropic(self, messages: list[dict[str, str]], max_tokens: int) -> LLMReply:
        system_parts: list[str] = []
        converse: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
            else:
                mapped = "assistant" if role == "assistant" else "user"
                converse.append({"role": mapped, "content": content})
        if not converse:
            converse = [{"role": "user", "content": ""}]
        payload: dict = {
            "model": self.settings.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": converse,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        url = join_url(self.settings.base_url, "messages")
        body, cost = _post_with_temperature_retry(
            url,
            _headers(self.settings, anthropic=True),
            payload,
            self.settings.timeout,
        )
        return LLMReply(text=_anthropic_text(body), cost_usd=cost)


def post_json(
    url: str,
    headers: Mapping[str, str],
    payload: dict,
    timeout: float,
) -> tuple[dict, str | None]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            cost = response.headers.get("X-Atmorouter-Cost")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400].replace("\n", " ")
        raise LLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"request to {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError(f"request to {url} timed out") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"provider returned non-JSON from {url}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("provider returned a non-object JSON body")
    return parsed, cost


def _post_with_temperature_retry(
    url: str,
    headers: Mapping[str, str],
    payload: dict,
    timeout: float,
) -> tuple[dict, str | None]:
    try:
        return post_json(url, headers, payload, timeout)
    except LLMError as exc:
        message = str(exc).lower()
        if "temperature" in message and "temperature" in payload:
            retried = dict(payload)
            retried.pop("temperature", None)
            return post_json(url, headers, retried, timeout)
        raise


def _headers(settings: Settings, *, anthropic: bool) -> dict[str, str]:
    key = settings.api_key or ""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"skimsearch/{__version__}",
        "Authorization": f"Bearer {key}",
    }
    if anthropic:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _openai_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("provider returned no chat choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if not isinstance(message, dict):
        raise LLMError("provider chat choice has no message")
    return _flatten_content(message.get("content"))


def _anthropic_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _flatten_content(content)
    raise LLMError("provider messages response has no content")


def _flatten_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)
