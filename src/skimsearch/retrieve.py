"""Rank skim cards and trim them to a token budget."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from skimsearch.norm import normalize_text
from skimsearch.store import load_cards
from skimsearch.tokens import estimate_tokens
from skimsearch.vectors import cosine, unpack_vector

# Question words add noise and almost never appear as symbols.
_STOP = {
    "the",
    "and",
    "for",
    "how",
    "does",
    "where",
    "what",
    "when",
    "with",
    "from",
    "this",
    "that",
    "are",
    "was",
    "were",
    "into",
    "via",
    "about",
    "which",
    "who",
    "why",
    "can",
    "could",
    "should",
    "would",
    "have",
    "has",
    "had",
    "not",
    "you",
    "your",
    "our",
    "their",
    "its",
    "using",
    "use",
    "used",
    "did",
    "do",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "is",
    "it",
    "we",
    "they",
    "be",
    "or",
    "if",
    "as",
    "at",
    "by",
    "than",
}

_EXT_NOISE = {
    "py",
    "js",
    "ts",
    "md",
    "go",
    "rs",
    "rb",
    "java",
    "cc",
    "cpp",
    "cs",
    "php",
    "kt",
    "json",
    "yml",
    "yaml",
    "toml",
    "html",
    "css",
    "sh",
    "txt",
}


@dataclass(frozen=True)
class DeckCard:
    rank: int
    path: str
    start_line: int
    end_line: int
    language: str
    kind: str
    symbol: str
    part: int
    score: float
    tokens: int
    text: str


def query_terms(query: str) -> list[str]:
    raw = normalize_text(query).split()
    terms: list[str] = []
    seen: set[str] = set()
    for token in raw:
        if len(token) < 2 or token in _STOP or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    if len(terms) > 1:
        filtered = [token for token in terms if token not in _EXT_NOISE]
        if filtered:
            terms = filtered
    return terms[:16]


def fts_query(terms: list[str]) -> str:
    pieces: list[str] = []
    if len(terms) >= 2:
        pieces.append('"' + " ".join(terms[:6]) + '"')
    pieces.extend(f'"{term}"' for term in terms)
    return " OR ".join(pieces)


def search_lexical(
    con: sqlite3.Connection,
    query: str,
    *,
    pool: int = 40,
) -> list[tuple[sqlite3.Row, float]]:
    terms = query_terms(query)
    if not terms:
        return []
    raw: dict[int, float] = {}
    try:
        found = con.execute(
            """
            SELECT rowid AS id, bm25(cards_fts, 8.0, 3.0, 1.5, 1.0) AS b
            FROM cards_fts
            WHERE cards_fts MATCH ?
            ORDER BY b
            LIMIT ?
            """,
            (fts_query(terms), pool),
        ).fetchall()
    except sqlite3.OperationalError:
        found = []
    for row in found:
        raw[int(row["id"])] = -float(row["b"])
    if not raw:
        for card_id in _like_fallback(con, terms, pool):
            raw[card_id] = 0.2
    return _decorate(con, raw, terms, query)


def search_probes(
    con: sqlite3.Connection,
    probes: list[str],
    *,
    pool: int = 40,
) -> list[tuple[sqlite3.Row, float]]:
    combined: dict[int, float] = {}
    rows: dict[int, sqlite3.Row] = {}
    hits: dict[int, int] = {}
    for probe in probes:
        for card, score in search_lexical(con, probe, pool=pool):
            card_id = int(card["id"])
            rows[card_id] = card
            hits[card_id] = hits.get(card_id, 0) + 1
            combined[card_id] = max(combined.get(card_id, 0.0), score)
    ranked = [
        (rows[card_id], score + 0.25 * (hits[card_id] - 1)) for card_id, score in combined.items()
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def search_embeddings(
    con: sqlite3.Connection,
    vector: list[float],
    *,
    pool: int = 40,
) -> list[tuple[sqlite3.Row, float]]:
    scored: list[tuple[int, float]] = []
    for row in con.execute("SELECT id, embedding FROM cards WHERE embedding IS NOT NULL"):
        other = unpack_vector(row["embedding"])
        if len(other) != len(vector):
            continue
        scored.append((int(row["id"]), cosine(vector, other)))
    scored.sort(key=lambda item: item[1], reverse=True)
    chosen = scored[:pool]
    cards = load_cards(con, [card_id for card_id, _score in chosen])
    return [(cards[card_id], score) for card_id, score in chosen if card_id in cards]


def diversify(
    ranked: list[tuple[sqlite3.Row, float]],
    *,
    per_file: int = 3,
) -> list[tuple[sqlite3.Row, float]]:
    picked: list[tuple[sqlite3.Row, float]] = []
    skipped: list[tuple[sqlite3.Row, float]] = []
    counts: dict[str, int] = {}
    for item in ranked:
        path = str(item[0]["path"])
        if counts.get(path, 0) >= per_file:
            skipped.append(item)
            continue
        picked.append(item)
        counts[path] = counts.get(path, 0) + 1
    return picked + skipped


def pack_deck(
    ranked: list[tuple[sqlite3.Row, float]],
    terms: list[str],
    *,
    budget: int,
    limit: int,
    card_tokens: int,
) -> tuple[list[DeckCard], int]:
    """Choose a short deck whose rendered form stays inside `budget`."""
    ordered = diversify(ranked)
    packed: list[DeckCard] = []
    used = 0
    for card, score in ordered:
        if len(packed) >= limit:
            break
        remaining = budget - used
        if packed and remaining < 12:
            break
        body, start_line, end_line = tighten(
            str(card["text"]),
            int(card["start_line"]),
            terms,
            max(8, min(card_tokens, remaining)),
        )
        draft = _render_block(
            rank=len(packed) + 1,
            path=str(card["path"]),
            start_line=start_line,
            end_line=end_line,
            kind=str(card["kind"]),
            symbol=str(card["symbol"]),
            language=str(card["language"]),
            part=int(card["part"]),
            score=score,
            body=body,
        )
        cost = estimate_tokens(draft)
        if cost > remaining:
            body, start_line, end_line = _shrink_to(
                str(card["text"]),
                int(card["start_line"]),
                terms,
                remaining,
            )
            draft = _render_block(
                rank=len(packed) + 1,
                path=str(card["path"]),
                start_line=start_line,
                end_line=end_line,
                kind=str(card["kind"]),
                symbol=str(card["symbol"]),
                language=str(card["language"]),
                part=int(card["part"]),
                score=score,
                body=body,
            )
            cost = estimate_tokens(draft)
            if cost > remaining and packed:
                continue
            if cost > remaining and not packed:
                body, start_line, end_line, draft, cost = _fit_single(
                    body,
                    start_line,
                    end_line,
                    budget=budget,
                    path=str(card["path"]),
                    kind=str(card["kind"]),
                    symbol=str(card["symbol"]),
                    language=str(card["language"]),
                    part=int(card["part"]),
                    score=score,
                )
        packed.append(
            DeckCard(
                rank=len(packed) + 1,
                path=str(card["path"]),
                start_line=start_line,
                end_line=end_line,
                language=str(card["language"]),
                kind=str(card["kind"]),
                symbol=str(card["symbol"]),
                part=int(card["part"]),
                score=score,
                tokens=cost,
                text=body,
            )
        )
        used += cost
        if used >= budget:
            break
    return packed, used


def tighten(
    text: str,
    start_line: int,
    terms: list[str],
    token_cap: int,
) -> tuple[str, int, int]:
    lines = text.splitlines() or [""]
    full_end = start_line + len(lines) - 1
    if estimate_tokens(text) <= token_cap:
        return text, start_line, full_end
    scores = [_line_score(line, terms) for line in lines]
    if any(scores):
        anchor = max(range(len(lines)), key=lambda index: (scores[index], -index))
    else:
        anchor = 0
    selected = {anchor}
    if lines and lines[0].strip() and anchor != 0:
        selected.add(0)

    def cost(indexes: set[int]) -> int:
        return estimate_tokens(_join_selected(lines, indexes))

    radius = 1
    while radius < len(lines):
        candidate = set(selected)
        low = max(0, anchor - radius)
        high = min(len(lines), anchor + radius + 1)
        candidate.update(range(low, high))
        if cost(candidate) > token_cap:
            break
        selected = candidate
        radius += 1
    body = _join_selected(lines, selected)
    if estimate_tokens(body) > token_cap:
        body = _char_trim(body, token_cap)
        return body, start_line + min(selected), start_line + max(selected)
    return body, start_line + min(selected), start_line + max(selected)


def render_card_block(card: DeckCard) -> str:
    return _render_block(
        rank=card.rank,
        path=card.path,
        start_line=card.start_line,
        end_line=card.end_line,
        kind=card.kind,
        symbol=card.symbol,
        language=card.language,
        part=card.part,
        score=card.score,
        body=card.text,
    )


def _decorate(
    con: sqlite3.Connection,
    raw: dict[int, float],
    terms: list[str],
    query: str,
) -> list[tuple[sqlite3.Row, float]]:
    if not raw:
        return []
    values = list(raw.values())
    low, high = min(values), max(values)
    cards = load_cards(con, list(raw))
    ranked: list[tuple[sqlite3.Row, float]] = []
    for card_id, base in raw.items():
        card = cards.get(card_id)
        if card is None:
            continue
        lexical = (base - low) / (high - low) if high > low else 1.0
        ranked.append((card, lexical + _bonus(card, terms, query)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _bonus(card: sqlite3.Row, terms: list[str], query: str) -> float:
    symbol = normalize_text(str(card["symbol"]))
    path = normalize_text(str(card["path"]))
    symbol_words = set(symbol.split())
    path_words = set(path.split())
    bonus = 0.0
    symbol_hits = 0
    for term in terms:
        if term in symbol_words:
            symbol_hits += 1
            bonus += 0.45
        if term in path_words:
            bonus += 0.2
    if terms and symbol_hits >= len(terms):
        bonus += 0.9
    query_norm = normalize_text(query)
    if query_norm and query_norm == symbol:
        bonus += 2.0
    elif query_norm and symbol and query_norm in symbol:
        bonus += 1.2
    if str(card["kind"]) in {"function", "class", "heading"}:
        bonus += 0.05
    return bonus


def _like_fallback(con: sqlite3.Connection, terms: list[str], pool: int) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for term in terms[:4]:
        needle = f"%{term}%"
        rows = con.execute(
            """
            SELECT id FROM cards
            WHERE lower(symbol) LIKE ? OR lower(path) LIKE ? OR lower(text) LIKE ?
            LIMIT ?
            """,
            (needle, needle, needle, pool),
        ).fetchall()
        for row in rows:
            card_id = int(row["id"])
            if card_id in seen:
                continue
            seen.add(card_id)
            found.append(card_id)
            if len(found) >= pool:
                return found
    return found


def _line_score(line: str, terms: list[str]) -> int:
    lowered = line.lower()
    return sum(1 for term in terms if term in lowered)


def _join_selected(lines: list[str], indexes: set[int]) -> str:
    ordered = sorted(indexes)
    parts: list[str] = []
    buffer: list[str] = []
    previous: int | None = None
    for index in ordered:
        if previous is not None and index != previous + 1 and buffer:
            parts.append("\n".join(buffer))
            buffer = []
        buffer.append(lines[index])
        previous = index
    if buffer:
        parts.append("\n".join(buffer))
    return "\n…\n".join(parts)


def _char_trim(text: str, token_cap: int) -> str:
    chars = max(8, token_cap * 4)
    if len(text) <= chars:
        return text
    return text[: chars - 1].rstrip() + "…"


def _render_block(
    *,
    rank: int,
    path: str,
    start_line: int,
    end_line: int,
    kind: str,
    symbol: str,
    language: str,
    part: int,
    score: float,
    body: str,
) -> str:
    part_bit = f"  part {part}" if part > 1 else ""
    header = (
        f"{rank}. {path}:{start_line}-{end_line}  {kind} {symbol}  "
        f"({language}, score {score:.2f}){part_bit}"
    )
    indented = "\n".join(f"    {line}" for line in (body.splitlines() or [""]))
    return header + "\n" + indented


def _shrink_to(
    text: str,
    start_line: int,
    terms: list[str],
    remaining: int,
) -> tuple[str, int, int]:
    cap = max(8, remaining // 2)
    return tighten(text, start_line, terms, cap)


def _fit_single(
    body: str,
    start_line: int,
    end_line: int,
    *,
    budget: int,
    path: str,
    kind: str,
    symbol: str,
    language: str,
    part: int,
    score: float,
) -> tuple[str, int, int, str, int]:
    """Shrink one card until its rendered block fits the whole budget."""
    draft = ""
    cost = budget + 1
    guard = 0
    while cost > budget and guard < 12:
        guard += 1
        if body:
            body = _char_trim(body, max(4, estimate_tokens(body) // 2))
        draft = _render_block(
            rank=1,
            path=path,
            start_line=start_line,
            end_line=end_line,
            kind=kind,
            symbol=symbol,
            language=language,
            part=part,
            score=score,
            body=body,
        )
        cost = estimate_tokens(draft)
        if estimate_tokens(body) <= 4:
            break
    return body, start_line, end_line, draft, cost
