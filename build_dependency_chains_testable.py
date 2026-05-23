"""
build_dependency_chains_testable.py

Thin wrapper around build_dependency_chains.py. Reuses ALL its resolution
logic (MANUAL_OVERRIDES, FILE_TYPES, SIMPLE_TYPES, NEVER_NULL_TYPES,
resolve_type, find_resolvable_subclass, etc.) by importing the module and
overriding only the input/output paths before calling main().

Inputs (testable variants):
  - extracted_metadata_testable.json   (produced by enrich_extracted_metadata_testable.py)
  - class_inventory_testable.json      (copy of class_inventory.json)

Output:
  - dependency_chains_testable.json

Usage:
    python build_dependency_chains_testable.py
"""

import build_dependency_chains as bdc


# Override the module-level paths the existing main() reads.
bdc.METADATA_FILE = (
    r"C:\Users\Harini\Documents\thesis_research"
    r"\test_generator\extracted_metadata_testable.json"
)
bdc.CLASS_INVENTORY = (
    r"C:\Users\Harini\Documents\thesis_research"
    r"\test_generator\class_inventory_testable.json"
)
bdc.OUTPUT_FILE = (
    r"C:\Users\Harini\Documents\thesis_research"
    r"\test_generator\dependency_chains_testable.json"
)

# UNCOVERED_ONLY=True is fine — the enrichment script sets has_developer_tests=False
# on every entry, so no methods get filtered out.

if __name__ == "__main__":
    bdc.main()
