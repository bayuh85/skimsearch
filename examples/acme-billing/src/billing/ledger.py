"""Append-only ledger for accounts receivable."""


def post_entry(ledger: list[dict], account: str, amount: float, memo: str) -> dict:
    entry = {"account": account, "amount": amount, "memo": memo}
    ledger.append(entry)
    return entry


def balance(ledger: list[dict], account: str) -> float:
    return round(sum(item["amount"] for item in ledger if item["account"] == account), 2)
