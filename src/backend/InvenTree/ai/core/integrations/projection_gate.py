"""A durable projection stop is additional to all existing read authorization."""


def recall_allowed(corpus: str) -> bool:
    """A missing/unavailable gate table cannot authorize provider work."""
    if corpus not in {"attachment", "media"}:
        return False
    try:
        from aichat.models import RagProjectionGate

        return not RagProjectionGate.objects.filter(corpus=corpus, blocked=True).exists()
    except Exception:
        return False
