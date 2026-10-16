"""Text and JSON renderers for decks, index reports, and status."""

from __future__ import annotations

import json
from dataclasses import dataclass

from skimsearch.config import Settings, mask_secret
from skimsearch.retrieve import DeckCard, render_card_block


@dataclass(frozen=True)
class IndexReport:
    root: str
    index_path: str
    files: int
    cards: int
    updated: int
    removed: int
    skipped: int
    embedded: int
    embed_model: str | None


@dataclass(frozen=True)
class SearchResult:
    query: str
    mode: str
    requested_mode: str
    budget: int
    used_tokens: int
    answer: str | None
    probes: list[str]
    notice: str | None
    cost: str | None
    cards: list[DeckCard]
    root: str


@dataclass(frozen=True)
class StatusReport:
    root: str
    index_path: str
    files: int
    cards: int
    stored_tokens: int
    indexed_at: str
    schema_version: str
    embed_model: str
    settings: Settings


def render_index(report: IndexReport) -> str:
    lines = [
        (
            f"indexed {report.files} files, {report.cards} cards "
            f"({report.updated} updated, {report.removed} removed, {report.skipped} skipped)"
        ),
        f"index: {report.index_path}",
    ]
    if report.embedded:
        model = report.embed_model or "embeddings"
        lines.append(f"embedded {report.embedded} cards with {model}")
    return "\n".join(lines) + "\n"


def render_status(report: StatusReport) -> str:
    settings = report.settings
    if settings.key_source and settings.api_key:
        key_line = f"{settings.key_source} {mask_secret(settings.api_key)}"
    else:
        key_line = "(missing)"
    lines = [
        f"project: {report.root}",
        f"index: {report.index_path}",
        f"schema: {report.schema_version}",
        f"files: {report.files}",
        f"cards: {report.cards}",
        f"stored_tokens: {report.stored_tokens}",
        f"indexed_at: {report.indexed_at or '(unknown)'}",
        f"embed_model: {report.embed_model}",
        f"provider: {settings.base_url}",
        f"model: {settings.model}",
        f"api_style: {settings.api_style}",
        f"api_key: {key_line}",
    ]
    return "\n".join(lines) + "\n"


def render_search(result: SearchResult) -> str:
    lines = [
        (
            f"skimsearch  mode={result.mode}  cards={len(result.cards)}  "
            f"tokens={result.used_tokens}/{result.budget}"
        )
    ]
    if result.probes:
        lines.append("probes: " + " | ".join(result.probes))
    if result.notice:
        lines.append("note: " + result.notice)
    if result.answer:
        lines.append("")
        lines.append(result.answer.strip())
    if not result.cards:
        lines.append("")
        lines.append("No cards matched. Try a symbol, a path hint, or --mode semantic.")
        return "\n".join(lines).rstrip() + "\n"
    lines.append("")
    for card in result.cards:
        lines.append(render_card_block(card))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_search_json(result: SearchResult) -> str:
    payload = {
        "query": result.query,
        "mode": result.mode,
        "requested_mode": result.requested_mode,
        "budget": result.budget,
        "used_tokens": result.used_tokens,
        "answer": result.answer,
        "probes": result.probes,
        "notice": result.notice,
        "cost": result.cost,
        "cards": [
            {
                "rank": card.rank,
                "path": card.path,
                "start_line": card.start_line,
                "end_line": card.end_line,
                "language": card.language,
                "kind": card.kind,
                "symbol": card.symbol,
                "part": card.part,
                "tokens": card.tokens,
                "score": round(card.score, 4),
                "text": card.text,
            }
            for card in result.cards
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
