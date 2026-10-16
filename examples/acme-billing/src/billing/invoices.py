"""Invoice totals and late fees for Acme billing."""

from datetime import date

GRACE_DAYS = 3
LATE_FEE_RATE = 0.015


class InvoiceBook:
    """Holds open invoices for one account."""

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.invoices: list[dict] = []


def apply_late_fee(invoice: dict, as_of: date) -> dict:
    """The late fee is calculated from the subtotal after the grace window."""
    if invoice.get("settled"):
        return invoice
    overdue = (as_of - invoice["due"]).days
    if overdue <= GRACE_DAYS:
        return invoice
    fee = round(invoice["subtotal"] * LATE_FEE_RATE, 2)
    updated = dict(invoice)
    updated["late_fee"] = fee
    updated["total"] = round(invoice["subtotal"] + fee, 2)
    return updated


def format_invoice_number(year: int, seq: int) -> str:
    return f"INV-{year}-{seq:05d}"
