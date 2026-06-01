from __future__ import annotations

import re

HIDDEN_WHITESPACE_PATTERN = re.compile(r"[\s\u200B\u200C\u200D\uFEFF]+")


def sanitize_text_input(value: str) -> str:
    """Clean arbitrary text input.

    This helper normalizes whitespace, removes hidden characters, and trims
    surrounding space. It does not preserve internal formatting beyond a single
    space for readability.
    """
    if not isinstance(value, str):
        raise TypeError("Expected a string for sanitization")

    cleaned = HIDDEN_WHITESPACE_PATTERN.sub(" ", value)
    return cleaned.strip()


def sanitize_public_key(value: str) -> str:
    """Normalize a public key or cryptographic address string.

    This helper removes all visible and hidden whitespace characters from the
    provided key so signatures and address comparisons are not broken by
    trailing line breaks or hidden spaces.
    """
    if not isinstance(value, str):
        raise TypeError("Expected a string public key")

    cleaned = value.replace("\u200B", "")
    cleaned = cleaned.replace("\u200C", "")
    cleaned = cleaned.replace("\u200D", "")
    cleaned = cleaned.replace("\uFEFF", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned
