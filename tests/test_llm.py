"""Provider client. HTTP is mocked; nothing here dials the network."""

from pathlib import Path

import pytest

from skimsearch.config import DEFAULT_BASE_URL, DEFAULT_MODEL, load_settings
from skimsearch.llm import LLMClient, LLMError, parse_json_object


def test_default_provider_is_atmorouter():
    settings = load_settings(environ={})
    assert settings.base_url == DEFAULT_BASE_URL == "https://atmorouter.dev/v1"
    assert settings.model == DEFAULT_MODEL
    assert settings.api_key is None
    assert settings.api_style == "openai"


def test_key_and_base_url_priority():
    settings = load_settings(
        environ={
            "OPENAI_API_KEY": "sk-other",
            "ATMOROUTER_API_KEY": "ar-primary",
            "OPENAI_BASE_URL": "https://example.com/v1",
            "SKIMSEARCH_BASE_URL": "https://gateway.example/v1/",
        }
    )
    assert settings.api_key == "ar-primary"
    assert settings.key_source == "ATMOROUTER_API_KEY"
    assert settings.base_url == "https://gateway.example/v1"


def test_openai_key_used_when_atmorouter_key_is_absent():
    settings = load_settings(
        environ={
            "OPENAI_API_KEY": "ar-from-openai-var",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
        }
    )
    assert settings.api_key == "ar-from-openai-var"
    assert settings.base_url == "https://api.openai.com/v1"


def test_dotenv_does_not_override_the_environment(tmp_path: Path):
    envfile = tmp_path / ".env"
    envfile.write_text(
        "ATMOROUTER_API_KEY=ar-from-file\nSKIMSEARCH_MODEL=custom/model\n",
        encoding="utf-8",
    )
    from_file = load_settings(environ={}, dotenv_paths=[envfile])
    assert from_file.api_key == "ar-from-file"
    assert from_file.model == "custom/model"
    pinned = load_settings(
        environ={"ATMOROUTER_API_KEY": "ar-from-env"},
        dotenv_paths=[envfile],
    )
    assert pinned.api_key == "ar-from-env"
    assert pinned.model == "custom/model"


def test_openai_chat_payload(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"choices": [{"message": {"content": "hello"}}]}, "0.0100"

    monkeypatch.setattr("skimsearch.llm.post_json", fake_post)
    client = LLMClient(load_settings(environ={"ATMOROUTER_API_KEY": "ar-test-key"}))
    reply = client.complete([{"role": "user", "content": "ping"}])
    assert reply.text == "hello"
    assert reply.cost_usd == "0.0100"
    assert captured["url"] == "https://atmorouter.dev/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer ar-test-key"
    assert captured["payload"]["model"] == DEFAULT_MODEL
    assert captured["payload"]["messages"][0]["content"] == "ping"


def test_temperature_is_retried_once(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    def fake_post(url, headers, payload, timeout):
        calls.append(payload)
        if "temperature" in payload:
            raise LLMError("HTTP 400 from x: unsupported temperature")
        return {"choices": [{"message": {"content": "ok"}}]}, None

    monkeypatch.setattr("skimsearch.llm.post_json", fake_post)
    client = LLMClient(load_settings(environ={"ATMOROUTER_API_KEY": "ar-test-key"}))
    reply = client.complete([{"role": "user", "content": "ping"}])
    assert reply.text == "ok"
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_anthropic_messages_payload(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["payload"] = payload
        return {"content": [{"type": "text", "text": "pong"}]}, None

    monkeypatch.setattr("skimsearch.llm.post_json", fake_post)
    settings = load_settings(
        environ={
            "ATMOROUTER_API_KEY": "ar-test-key",
            "SKIMSEARCH_API_STYLE": "anthropic",
            "SKIMSEARCH_MODEL": "ag/claude-opus-4-6-thinking",
        }
    )
    reply = LLMClient(settings).complete(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert reply.text == "pong"
    assert captured["url"] == "https://atmorouter.dev/v1/messages"
    assert captured["headers"]["x-api-key"] == "ar-test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["system"] == "be brief"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["payload"]["model"] == "ag/claude-opus-4-6-thinking"


def test_embeddings_preserve_index_order(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }, None

    monkeypatch.setattr("skimsearch.llm.post_json", fake_post)
    client = LLMClient(load_settings(environ={"SKIMSEARCH_API_KEY": "ar-embed"}))
    vectors = client.embed(["first", "second"], "text-embedding-3-small")
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert captured["url"] == "https://atmorouter.dev/v1/embeddings"
    assert captured["payload"]["input"] == ["first", "second"]


def test_parse_json_object_accepts_fenced_and_nested_braces():
    raw = '```json\n{"answer": "use a } brace", "card_ids": [1, 2]}\n```'
    parsed = parse_json_object(raw)
    assert parsed["answer"] == "use a } brace"
    assert parsed["card_ids"] == [1, 2]


def test_bad_api_style_is_rejected():
    with pytest.raises(Exception, match="SKIMSEARCH_API_STYLE"):
        load_settings(environ={"SKIMSEARCH_API_STYLE": "xml"})
