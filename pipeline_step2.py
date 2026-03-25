#!/usr/bin/env python3
"""
Pipeline Step 2: Filter trivial methods, fix status, clean signatures.
"""

from pipeline.config import OUT_DIR
from pipeline.filters import apply_filters, clean_methods
from pipeline import io


def main() -> None:
    # Load
    data = io.load_metadata_json(OUT_DIR / "extracted_metadata.json")
    print(f"Loaded {len(data)} methods\n")

    # Filter
    filtered, results = apply_filters(data)
    for r in results:
        print(f"  {r.label}")
        print(f"    Removed: {r.removed}   Remaining: {r.remaining}\n")
    print(f"Total removed: {len(data) - len(filtered)}  |  Final: {len(filtered)}")

    # Clean
    status_fixes, sigs_cleaned = clean_methods(filtered)
    print(f"\nStatus fixes: {status_fixes}   Signatures cleaned: {sigs_cleaned}")

    # Save
    io.save_names_csv(filtered,           OUT_DIR / "filtered_public_methods.csv")
    io.save_metadata_json_raw(filtered,   OUT_DIR / "extracted_metadata_final.json")
    io.save_summary_csv(filtered,         OUT_DIR / "extraction_summary_final.csv")

    # Stats
    total    = len(filtered)
    has_body = sum(1 for m in filtered if m.get("body"))
    has_doc  = sum(1 for m in filtered if m.get("javadoc"))
    has_snip = sum(1 for m in filtered if m.get("usage_snippets"))
    body_nf  = sum(1 for m in filtered if m.get("status") == "BODY_NOT_FOUND")

    print(f"\n=== Final Stats ===")
    print(f"  Total   : {total}")
    print(f"  Body    : {has_body}  ({has_body/total*100:.1f}%)")
    print(f"  Javadoc : {has_doc}  ({has_doc/total*100:.1f}%)")
    print(f"  Snippets: {has_snip}  ({has_snip/total*100:.1f}%)")
    print(f"  BODY_NOT_FOUND: {body_nf}")


if __name__ == "__main__":
    main()
