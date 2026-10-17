"""Label list helpers."""

from __future__ import annotations


def append_label(label: str, labels: list[str] = []) -> list[str]:
    """Append ``label`` to ``labels`` and return the list.

    When ``labels`` is omitted, the call is documented to start from a fresh
    empty list.
    """
    labels.append(label)
    return labels
