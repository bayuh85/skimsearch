"""Folding source into skim cards."""

from skimsearch.fold import fold_source

PYTHON_SAMPLE = '''"""Billing totals."""

LATE_FEE_RATE = 0.015


class Invoice:
    """A bill."""

    @trace
    def apply_late_fee(self, as_of):
        """The late fee is calculated from the subtotal."""
        return self.subtotal


def format_invoice_number(year, seq):
    return f"INV-{year}-{seq:05d}"


LIMIT = 10
'''


def test_python_symbols_and_decorators():
    cards = fold_source("src/billing/invoices.py", PYTHON_SAMPLE)
    symbols = [card.symbol for card in cards]
    assert "Invoice" in symbols
    assert "Invoice.apply_late_fee" in symbols
    assert "format_invoice_number" in symbols
    method = next(card for card in cards if card.symbol == "Invoice.apply_late_fee")
    assert "@trace" in method.text
    assert method.kind == "function"
    assert method.start_line >= 1
    intro = next(card for card in cards if card.symbol == "(intro)")
    assert "LATE_FEE_RATE" in intro.text
    trailing = next(card for card in cards if "LIMIT = 10" in card.text)
    assert trailing.symbol == "(module)"


def test_python_covers_every_nonblank_line():
    _assert_covers("pkg/mod.py", PYTHON_SAMPLE)


def test_markdown_headings():
    text = "# Billing\n\nIntro line.\n\n## Late fees\n\nA fee applies.\n"
    cards = fold_source("docs/billing.md", text)
    symbols = [card.symbol for card in cards]
    assert symbols[0] == "Billing"
    assert "Late fees" in symbols
    late = next(card for card in cards if card.symbol == "Late fees")
    assert "A fee applies." in late.text
    assert late.kind == "heading"
    _assert_covers("docs/billing.md", text)


def test_javascript_top_level_declarations():
    text = """
export function createInvoice(id) {
  return { id };
}

export class Ledger {
  post(entry) {
    return entry;
  }
}

const formatTotal = (n) => n.toFixed(2);
""".strip()
    cards = fold_source("src/billing.js", text)
    symbols = {card.symbol for card in cards}
    assert "createInvoice" in symbols
    assert "Ledger" in symbols
    assert "formatTotal" in symbols
    _assert_covers("src/billing.js", text)


def test_go_function():
    text = (
        "package billing\n\n"
        "func ApplyLateFee(subtotal float64) float64 {\n"
        "    return subtotal * 0.015\n"
        "}\n"
    )
    cards = fold_source("fee.go", text)
    assert any(card.symbol == "ApplyLateFee" and card.kind == "function" for card in cards)
    _assert_covers("fee.go", text)


def test_long_text_is_windowed():
    lines = [f"line {index} marker" for index in range(1, 201)]
    text = "\n".join(lines)
    cards = fold_source("notes/log.txt", text)
    assert len(cards) > 1
    assert cards[-1].end_line == 200
    assert cards[0].part == 1
    assert cards[1].part == 2
    assert any("line 200 marker" in card.text for card in cards)


def test_binary_extension_is_not_a_language():
    assert fold_source("assets/logo.png", "not really") == []


def _assert_covers(rel: str, text: str) -> None:
    cards = fold_source(rel, text)
    covered: set[int] = set()
    for card in cards:
        covered.update(range(card.start_line, card.end_line + 1))
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            assert number in covered, line
