#!/usr/bin/env python3
"""
Extract extracted_metadata_final-shaped records for the methods listed in
testable_methods_moderate_complex.json.

Reuses pipeline/java_parser.py:extract_all_metadata to re-derive signature,
body, javadoc, and usage snippets from the local PDFBOX-v5 source tree.
The .und database and metrics CSV are NOT used — the input list is already
curated. Overloads bind to declarations in source-file order (no tie-breaking
by parameter count).

Run from the thesis_research/ directory:
    python -m test_generator.extract_for_testable_methods
or:
    python test_generator/extract_for_testable_methods.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the sibling `pipeline` package importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.java_parser import extract_all_metadata
from pipeline.filters import clean_methods
from pipeline import io


THESIS_ROOT = Path(__file__).resolve().parent.parent
INPUT       = THESIS_ROOT / "test_generator" / "testable_methods_moderate_complex.json"
SRC_ROOT    = THESIS_ROOT / "PDFBOX-v5" / "pdfbox" / "src" / "main" / "java"
OUTPUT      = THESIS_ROOT / "PDFBOX-v5" / "testable_methods_extracted_metadata.json"


def main() -> None:
    print(f"Input    : {INPUT}")
    print(f"Src root : {SRC_ROOT}")
    print(f"Output   : {OUTPUT}\n")

    with INPUT.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    print(f"Loaded {len(raw)} entries\n")

    rows = [{"Name": e["longname"], "Kind": e["kind"]} for e in raw]

    print("=== Extracting metadata from source ===")
    entries, stats = extract_all_metadata(rows, SRC_ROOT)

    dicts = [e.to_dict() for e in entries]
    status_fixes, sigs_cleaned = clean_methods(dicts)
    print(f"\nStatus fixes: {status_fixes}   Signatures cleaned: {sigs_cleaned}")

    io.save_metadata_json_raw(dicts, OUTPUT)

    has_body = sum(1 for m in dicts if m.get("body"))
    has_doc  = sum(1 for m in dicts if m.get("javadoc"))
    has_snip = sum(1 for m in dicts if m.get("usage_snippets"))
    print(f"\n=== Summary ===")
    print(f"  Total methods       : {stats.total}")
    print(f"  OK (body extracted) : {stats.ok}")
    print(f"  FILE_NOT_FOUND      : {stats.file_not_found}")
    print(f"  BODY_NOT_FOUND      : {stats.body_not_found}")
    print(f"  Has Javadoc         : {has_doc}")
    print(f"  Has snippets        : {has_snip}")
    print(f"  Has body            : {has_body}")
    print(f"\nWrote {len(dicts)} entries -> {OUTPUT}")


if __name__ == "__main__":
    main()
