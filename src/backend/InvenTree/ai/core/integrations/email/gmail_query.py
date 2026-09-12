"""Gmail-specific search translation for the legacy adapter."""

from .provider import EmailQuery


def build_gmail_query(query: EmailQuery) -> str:
    """
    Build a Gmail search query string from EmailQuery.

    Args:
        query: The EmailQuery object.

    Returns:
        Gmail query string.
    """
    parts: list[str] = []

    if query.query:
        parts.append(query.query)

    if query.from_address:
        parts.append(f"from:{query.from_address}")

    if query.to_address:
        parts.append(f"to:{query.to_address}")

    if query.subject:
        parts.append(f"subject:{query.subject}")

    if query.has_attachment is True:
        parts.append("has:attachment")

    if query.is_unread is True:
        parts.append("is:unread")
    elif query.is_unread is False:
        parts.append("is:read")

    if query.after_date:
        parts.append(f"after:{query.after_date.strftime('%Y/%m/%d')}")

    if query.before_date:
        parts.append(f"before:{query.before_date.strftime('%Y/%m/%d')}")

    if query.label:
        parts.append(f"label:{query.label}")

    return " ".join(parts)
