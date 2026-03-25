"""Run Understand analysis and extract public methods from the metrics CSV."""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from .config import PUBLIC_KINDS


def run_analysis(db: Path) -> None:
    """Run `und analyze` then `und metrics` on the database, abort on failure."""
    for cmd in [["und", "analyze", str(db)], ["und", "metrics", str(db)]]:
        print(f"\n>>> {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode != 0:
            sys.exit(f"Command failed (exit {result.returncode}): {' '.join(cmd)}")
        print(f"<<< Done")


def load_public_methods(db: Path, out_path: Path) -> list[dict[str, str]]:
    """
    Read the metrics CSV produced by `und metrics`, filter to public method
    kinds, drop test methods, save the result, and return the filtered rows.
    """
    csv_path = _find_metrics_csv(db)
    print(f"  Reading metrics: {csv_path}")

    rows: list[dict[str, str]] = []
    kind_counts: dict[str, int] = defaultdict(int)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            kind = row.get("Kind", "").strip()
            name = row.get("Name", "").strip()
            if kind not in PUBLIC_KINDS or "Test" in name:
                continue
            rows.append({"Kind": kind, "Name": name})
            kind_counts[kind] += 1

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Kind", "Name"])
        writer.writeheader()
        writer.writerows(rows)

    for kind in sorted(kind_counts):
        print(f"  {kind:<35} : {kind_counts[kind]}")
    print(f"\n  Total: {len(rows)}  →  {out_path.name}")

    return rows


def _find_metrics_csv(db: Path) -> Path:
    candidate = db.with_suffix(".csv")
    if candidate.exists():
        return candidate
    matches = list(db.parent.glob("*.csv"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No metrics CSV found next to {db}")
