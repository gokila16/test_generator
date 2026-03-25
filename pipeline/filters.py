"""
Step 2 filter rules and data-cleaning for extracted method metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

Method = dict  # raw JSON dict from extracted_metadata.json

TRIVIAL_PREFIXES = ("get", "set", "is", "has")
TRIVIAL_NAMES = frozenset({
    "toString", "hashCode", "equals", "compareTo",
    "clone", "finalize", "notify", "wait",
})


@dataclass
class FilterResult:
    label: str
    removed: int
    remaining: int


def apply_filters(methods: list[Method]) -> tuple[list[Method], list[FilterResult]]:
    """Apply all three rules in sequence; return survivors and per-rule results."""
    rules: list[tuple[str, Callable[[Method], bool]]] = [
        ("Rule 1 — get/set/is/has with body ≤ 5 lines",  _is_trivial_accessor),
        ("Rule 2 — boilerplate (toString/hashCode/...)", _is_boilerplate),
        ("Rule 3 — body ≤ 2 lines",                      _is_too_short),
    ]
    remaining = list(methods)
    results = []
    for label, predicate in rules:
        before = len(remaining)
        remaining = [m for m in remaining if not predicate(m)]
        results.append(FilterResult(label, removed=before - len(remaining), remaining=len(remaining)))
    return remaining, results


def clean_methods(methods: list[Method]) -> tuple[int, int]:
    """
    Mutate methods in-place:
      - Fix OK status on entries with no body.
      - Strip opening brace from signatures.
    Returns (status_fixes, signatures_cleaned).
    """
    status_fixes = sigs_cleaned = 0
    for m in methods:
        if not m.get("body") and m.get("status") == "OK":
            m["status"] = "BODY_NOT_FOUND"
            status_fixes += 1
        original = m.get("signature", "")
        cleaned = re.sub(r"\s*\{\s*$", "", original.rstrip()).rstrip()
        if cleaned != original:
            m["signature"] = cleaned
            sigs_cleaned += 1
    return status_fixes, sigs_cleaned


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def _line_count(m: Method) -> int:
    return (m.get("body") or "").count("\n")

def _is_trivial_accessor(m: Method) -> bool:
    name = m.get("method_name", "")
    return any(name.startswith(p) for p in TRIVIAL_PREFIXES) and _line_count(m) <= 5

def _is_boilerplate(m: Method) -> bool:
    return m.get("method_name", "") in TRIVIAL_NAMES

def _is_too_short(m: Method) -> bool:
    return _line_count(m) <= 2
