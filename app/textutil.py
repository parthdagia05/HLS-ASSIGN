"""Small text helpers shared by the loader and the suggestion service.

Keeping normalisation in one place guarantees that the way we store queries and
the way we look them up by prefix can never drift apart.
"""

from __future__ import annotations


def normalize_query(text: str) -> str:
    """Trim, lowercase, and collapse internal whitespace.

    "  IPhone  15 " -> "iphone 15". Both the dataset loader and the /suggest
    endpoint run user/dataset text through this, so casing and stray spaces
    never cause a miss.
    """
    return " ".join((text or "").split()).lower()


def like_prefix_pattern(prefix: str) -> str:
    r"""Turn a user prefix into a safe SQL LIKE pattern matching `prefix%`.

    LIKE treats %, _ and \ specially. If a user types "100%" we must match a
    literal percent sign, not "any characters". We escape those three
    characters and append % as the only wildcard. Use with: LIKE pattern ESCAPE '\'
    """
    escaped = (
        prefix.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return escaped + "%"
