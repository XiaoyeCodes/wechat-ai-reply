"""Lossless transport splitting; never summarize or reformat generated text."""


def receipt_text(text):
    """WeChat trims bubble edges and may expose CRLF through accessibility.

    Use only for delivery receipts, never for the queued/generated message.
    Interior whitespace remains significant (including Markdown table/code rows).
    """
    return text.replace('\r\n', '\n').strip()


def split_text(text, limit=1999):
    parts = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        cut = cut + 1 if cut >= limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts
