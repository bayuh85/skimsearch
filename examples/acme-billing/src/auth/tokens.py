"""Issue console tokens."""


def issue_session(subject: str, issued_at: int) -> str:
    return f"sess:{issued_at}:{subject}"
