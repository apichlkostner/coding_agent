"""Label list helpers."""

from __future__ import annotations

_DEFAULT_LABELS: list[str] = []


def append_label(label: str, labels: list[str] = _DEFAULT_LABELS) -> list[str]:
    """Append ``label`` to ``labels`` and return the list.

    When ``labels`` is omitted, the call starts from a fresh empty list. An
    explicitly supplied list is mutated in place and returned unchanged.
    """
    if labels is _DEFAULT_LABELS:
        labels = []
    labels.append(label)
    return labels
