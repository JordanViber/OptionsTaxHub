"""Account labels for one Robinhood CSV export.

Exports only. No broker API. A missing label is the historical single-file
upload and stays in one book with today's wash-sale math.
"""

from __future__ import annotations

from typing import TypeVar

ACCOUNT_LABELS: dict[str, str] = {
    "individual": "Individual",
    "ira_traditional": "IRA (traditional)",
    "ira_roth": "IRA (Roth)",
    "other": "Other/agentic",
}

IRA_ACCOUNT_LABELS = frozenset({"ira_traditional", "ira_roth"})

_ALIASES = {
    "individual": "individual",
    "ira_traditional": "ira_traditional",
    "traditional_ira": "ira_traditional",
    "ira_roth": "ira_roth",
    "roth_ira": "ira_roth",
    "other": "other",
    "other_agentic": "other",
    "agentic": "other",
}

T = TypeVar("T")


def normalize_account_label(raw: str | None) -> str | None:
    """Return a canonical label, or None when the upload did not name one."""
    if raw is None:
        return None
    key = (
        raw.strip()
        .lower()
        .replace("-", "_")
        .replace("/", "_")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
    )
    if not key:
        return None
    canonical = _ALIASES.get(key)
    if canonical is None:
        raise ValueError(raw.strip())
    return canonical


def account_display_name(label: str | None) -> str | None:
    if not label:
        return None
    return ACCOUNT_LABELS.get(label)


def is_ira_account(label: str | None) -> bool:
    return bool(label) and label in IRA_ACCOUNT_LABELS


def account_key(item: object) -> str:
    raw = getattr(item, "account_label", None)
    if raw is None:
        return ""
    return str(raw).strip()


def group_by_account(items: list[T]) -> list[list[T]]:
    """One group when every row shares a label, including all unlabeled."""
    order: list[str] = []
    buckets: dict[str, list[T]] = {}
    for item in items:
        key = account_key(item)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(item)
    if len(order) <= 1:
        return [list(items)]
    return [buckets[key] for key in order]
