"""Index and search without the network."""

import json
from pathlib import Path

from skimsearch.config import load_settings
from skimsearch.engine import index_project, search_project, status_project
from skimsearch.llm import LLMReply
from skimsearch.store import connect, index_path
from skimsearch.vectors import pack_vector

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme-billing"


class ScriptedModel:
    def __init__(self, replies: list[tuple[str, str | None]]):
        self.replies = list(replies)
        self.calls: list = []

    def complete(self, messages, max_tokens=600):
        self.calls.append(messages)
        text, cost = self.replies.pop(0)
        return LLMReply(text, cost)

    def embed(self, texts, model):
        self.calls.append(("embed", model, list(texts)))
        return [[1.0, 0.0] for _ in texts]


def test_example_repo_finds_late_fee_and_skips_secrets():
    report = index_project(EXAMPLE, force=True)
    assert report.files >= 5
    assert report.cards >= 5
    result = search_project(EXAMPLE, "where is the late fee calculated", budget=800)
    symbols = [card.symbol for card in result.cards]
    assert any("apply_late_fee" in symbol for symbol in symbols)
    blob = "\n".join(card.text for card in result.cards)
    assert "SECRET_TOKEN" not in blob
    assert "leftpad-marker" not in blob
    assert "node-modules-should-not-be-indexed" not in blob
    assert result.used_tokens <= result.budget
    paths = {card.path for card in result.cards}
    assert all(not path.startswith("vendor/") for path in paths)
    assert all("node_modules" not in path for path in paths)


def test_session_query_hits_verify_session():
    index_project(EXAMPLE, force=True)
    result = search_project(EXAMPLE, "verify_session expired", budget=600)
    assert any(card.symbol == "verify_session" for card in result.cards)


def test_budget_trims_the_deck(tmp_path: Path):
    _write_many(tmp_path)
    index_project(tmp_path)
    wide = search_project(tmp_path, "marker_fee", budget=4000, limit=8)
    assert len(wide.cards) >= 3
    assert "marker_fee_body_unique" in wide.cards[0].text or any(
        "marker_fee_body_unique" in card.text for card in wide.cards
    )
    tight = search_project(tmp_path, "marker_fee", budget=90, limit=8)
    assert tight.cards
    assert len(tight.cards) < len(wide.cards)
    assert tight.used_tokens <= tight.budget


def test_json_shape(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    result = search_project(tmp_path, "apply_late_fee", budget=500)
    from skimsearch.render import render_search_json

    payload = json.loads(render_search_json(result))
    assert payload["mode"] == "lexical"
    assert payload["query"] == "apply_late_fee"
    assert payload["cards"]
    card = payload["cards"][0]
    for key in ("rank", "path", "start_line", "end_line", "symbol", "text", "tokens", "score"):
        assert key in card
    assert card["symbol"] == "apply_late_fee"


def test_special_characters_do_not_break_fts(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    result = search_project(tmp_path, 'token: "fee" AND (late) OR apply_*', budget=400)
    assert any("apply_late_fee" in card.symbol for card in result.cards)


def test_incremental_update_and_delete(tmp_path: Path):
    source = tmp_path / "fee.py"
    source.write_text("def apply_late_fee():\n    return 1\n", encoding="utf-8")
    first = index_project(tmp_path)
    assert first.updated == 1
    second = index_project(tmp_path)
    assert second.updated == 0
    assert second.files == 1
    source.write_text("def renamed_fee():\n    return 2\n", encoding="utf-8")
    third = index_project(tmp_path)
    assert third.updated == 1
    renamed = search_project(tmp_path, "renamed_fee")
    assert any(card.symbol == "renamed_fee" for card in renamed.cards)
    stale = search_project(tmp_path, "apply_late_fee")
    assert all(card.symbol != "apply_late_fee" for card in stale.cards)
    source.unlink()
    fourth = index_project(tmp_path)
    assert fourth.removed == 1
    assert fourth.files == 0


def test_search_from_subdirectory_finds_parent_index(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    nested = tmp_path / "src"
    nested.mkdir()
    result = search_project(nested, "apply_late_fee")
    assert result.root == str(tmp_path.resolve())
    assert result.cards


def test_status_reports_provider(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    status = status_project(
        tmp_path,
        settings=load_settings(environ={"ATMOROUTER_API_KEY": "ar-example-key"}),
    )
    assert status.files == 1
    assert status.settings.base_url == "https://atmorouter.dev/v1"
    assert status.settings.api_key == "ar-example-key"
    assert index_path(tmp_path).is_file()


def test_ask_mode_uses_model_and_cites_a_card(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    model = ScriptedModel(
        [
            ('{"probes":["apply_late_fee","late fee"]}', "0.000100"),
            (
                '{"answer":"Late fees are calculated in apply_late_fee.","card_ids":[1]}',
                "0.000200",
            ),
        ]
    )
    result = search_project(tmp_path, "where is the late fee calculated", mode="ask", client=model)
    assert result.mode == "ask"
    assert result.answer is not None
    assert "apply_late_fee" in result.answer
    assert len(result.cards) == 1
    assert "apply_late_fee" in result.cards[0].symbol
    assert result.probes[0]
    assert result.cost == "0.000300"
    assert result.used_tokens <= result.budget
    assert model.calls


def test_semantic_mode_expands_probes(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    model = ScriptedModel([('{"probes":["apply_late_fee"]}', None)])
    result = search_project(tmp_path, "penalty on overdue invoices", mode="semantic", client=model)
    assert result.mode == "semantic"
    assert "apply_late_fee" in result.probes
    assert result.answer is None
    assert any("apply_late_fee" in card.symbol for card in result.cards)


def test_ask_without_key_falls_back(tmp_path: Path):
    _write_pair(tmp_path)
    index_project(tmp_path)
    settings = load_settings(environ={})
    result = search_project(
        tmp_path,
        "apply_late_fee",
        mode="ask",
        settings=settings,
    )
    assert result.mode == "lexical-fallback"
    assert result.notice is not None
    assert "ATMOROUTER_API_KEY" in result.notice
    assert result.cards


def test_embedding_rank_prefers_the_closer_vector(tmp_path: Path):
    _write_pair(tmp_path)
    (tmp_path / "other.py").write_text(
        "def unrelated_helper():\n    return 'other'\n",
        encoding="utf-8",
    )
    index_project(tmp_path)
    con = connect(tmp_path)
    try:
        for row in con.execute("SELECT id, symbol FROM cards"):
            vector = [1.0, 0.0] if "apply_late_fee" in str(row["symbol"]) else [0.0, 1.0]
            con.execute(
                "UPDATE cards SET embedding = ? WHERE id = ?",
                (pack_vector(vector), int(row["id"])),
            )
        con.commit()
    finally:
        con.close()
    model = ScriptedModel([])
    result = search_project(tmp_path, "zzzz", mode="semantic", client=model)
    assert result.mode == "semantic"
    assert result.cards
    assert "apply_late_fee" in result.cards[0].symbol
    assert model.calls and model.calls[0][0] == "embed"


def test_empty_query_and_missing_index(tmp_path: Path):
    from skimsearch.errors import IndexMissing, SkimError

    try:
        search_project(tmp_path, "   ")
        raise AssertionError("expected SkimError")
    except SkimError as exc:
        assert "empty" in str(exc)
    try:
        search_project(tmp_path, "fees")
        raise AssertionError("expected IndexMissing")
    except IndexMissing as exc:
        assert "skimsearch index" in str(exc)


def _write_pair(root: Path) -> None:
    source = root / "invoices.py"
    source.write_text(
        "def apply_late_fee(invoice):\n"
        "    # The late fee is calculated from the subtotal.\n"
        "    return invoice['subtotal'] * 0.015\n",
        encoding="utf-8",
    )


def _write_many(root: Path) -> None:
    chunks = []
    for index in range(6):
        chunks.append(
            f"def marker_fee_{index}():\n"
            f"    return 'marker_fee_body_unique {index} ' + ('pad ' * 30)\n"
        )
    (root / "fees.py").write_text("\n\n".join(chunks), encoding="utf-8")
