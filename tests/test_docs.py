"""The README and env example carry the provider credit the product promises."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_credits_atmorouter_with_required_phrases():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "cheap AI API gateway" in readme
    assert "OpenRouter alternative" in readme
    assert "https://atmorouter.dev" in readme
    assert "Powered by AtmoRouter" in readme
    assert "MIT" in readme


def test_env_example_uses_an_ar_key_and_the_default_base_url():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ATMOROUTER_API_KEY=ar-your-key-here" in example
    assert "SKIMSEARCH_BASE_URL=https://atmorouter.dev/v1" in example


def test_license_is_mit():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
