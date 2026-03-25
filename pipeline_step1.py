#!/usr/bin/env python3
"""
Pipeline Step 1: Run Understand analysis, filter public methods, extract metadata.
"""

from pipeline.config import UND_DB, SRC_ROOT, OUT_DIR
from pipeline.understand import run_analysis, load_public_methods
from pipeline.java_parser import extract_all_metadata
from pipeline import io


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Run Understand
    print("\n=== Step 1: Understand Analysis ===")
    run_analysis(UND_DB)

    # 2. Filter public methods from metrics CSV
    print("\n=== Step 2: Filter Public Methods ===")
    public_methods = load_public_methods(UND_DB, OUT_DIR / "public_methods.csv")

    # 3. Extract metadata from Java source
    print("\n=== Step 3: Extract Metadata ===")
    entries, stats = extract_all_metadata(public_methods, SRC_ROOT)

    io.save_metadata_json(entries, OUT_DIR / "extracted_metadata.json")
    io.save_summary_csv(entries,   OUT_DIR / "extraction_summary.csv")

    print(f"\n=== Summary ===")
    print(f"  Total methods       : {stats.total}")
    print(f"  OK (body extracted) : {stats.ok}")
    print(f"  Has Javadoc         : {stats.has_javadoc}")
    print(f"  Has snippets        : {stats.has_snippets}")
    print(f"  FILE_NOT_FOUND      : {stats.file_not_found}")
    print(f"  BODY_NOT_FOUND      : {stats.body_not_found}")


if __name__ == "__main__":
    main()
