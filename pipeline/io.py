"""Read and write pipeline data files (JSON and CSV)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import MethodEntry

SUMMARY_FIELDS = [
    "full_name", "class_name", "method_name", "kind",
    "has_body", "has_javadoc", "has_snippets", "snippet_count", "status",
]


def save_methods_csv(methods: list[dict], path: Path) -> None:
    """Save Kind + Name rows (output of step 1 filtering)."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Kind", "Name"])
        writer.writeheader()
        writer.writerows(methods)


def save_names_csv(entries: list, path: Path) -> None:
    """Save a single-column CSV of full_names."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["full_name"])
        for e in entries:
            name = e["full_name"] if isinstance(e, dict) else e.full_name
            writer.writerow([name])


def save_metadata_json(entries: list[MethodEntry], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump([e.to_dict() for e in entries], fh, indent=2, ensure_ascii=False)


def save_metadata_json_raw(entries: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)


def save_summary_csv(entries: list, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for e in entries:
            row = e.to_summary_row() if isinstance(e, MethodEntry) else _dict_to_summary(e)
            writer.writerow(row)


def load_metadata_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _dict_to_summary(m: dict) -> dict:
    return {
        "full_name":     m["full_name"],
        "class_name":    m.get("class_name", ""),
        "method_name":   m.get("method_name", ""),
        "kind":          m.get("kind", ""),
        "has_body":      bool(m.get("body")),
        "has_javadoc":   bool(m.get("javadoc")),
        "has_snippets":  bool(m.get("usage_snippets")),
        "snippet_count": len(m.get("usage_snippets") or []),
        "status":        m.get("status", ""),
    }
