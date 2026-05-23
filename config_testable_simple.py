"""
config_testable_simple.py

Runtime shim that retargets pipeline_step3_simple at the `*_simple*`
artifacts without modifying config.py or pipeline_step3_simple.py.

Imports `config`, overrides path constants in place, isolates outputs into a
parallel `simple/` subtree, and invokes pipeline_step3_simple.run_pipeline()
with the same --skip-file argument the original entrypoint accepts. Also
imports maven_runner_testable so the same Maven patches that fix Compiler
Plugin 3.14.0 + PDFBox's pom apply to this run too.

Usage (from test_generator/):
    python config_testable_simple.py
    python config_testable_simple.py --skip-file <path-to-skip.json>
"""

import argparse
import json
import os

import config
import maven_runner_testable  # noqa: F401  patches src.maven_runner.subprocess


# --- Override input paths --------------------------------------------------

config.INPUT_JSON = os.path.join(
    config.GENERATOR_DIR, "extracted_metadata_simple.json"
)

# Reuse the testable variants if they exist (same source tree, no per-run delta);
# class_inventory is source-walk-derived and not method-specific.
_class_inv_testable  = os.path.join(config.GENERATOR_DIR, "class_inventory_testable.json")
_class_inv_default   = os.path.join(config.GENERATOR_DIR, "class_inventory.json")
config.CLASS_INVENTORY_FILE = (
    _class_inv_testable if os.path.exists(_class_inv_testable) else _class_inv_default
)

# Dependency-chains is unused by the simple pipeline (no dep-chain prompt),
# but loaded by context_loader for compatibility — point it at any existing
# variant so load_context_data doesn't warn.
_dep_chains_testable = os.path.join(config.GENERATOR_DIR, "dependency_chains_testable.json")
_dep_chains_default  = os.path.join(config.GENERATOR_DIR, "dependency_chains.json")
config.DEPENDENCY_CHAINS_FILE = (
    _dep_chains_testable if os.path.exists(_dep_chains_testable) else _dep_chains_default
)


# --- Isolate outputs into a parallel `simple/` tree ------------------------

SIMPLE_RUN_DIR = os.path.join(config.BASE_DIR, "simple")

config.GENERATED_TESTS_DIR = os.path.join(config.PDFBOX_DIR, "generated_tests_simple")
config.PROMPTS_DIR         = os.path.join(SIMPLE_RUN_DIR, "prompts")
config.RESPONSES_DIR       = os.path.join(SIMPLE_RUN_DIR, "responses")
config.PLANS_DIR           = os.path.join(SIMPLE_RUN_DIR, "plans")
config.RESULTS_DIR         = os.path.join(SIMPLE_RUN_DIR, "results")
config.RESULTS_JSON        = os.path.join(config.RESULTS_DIR, "results.json")
config.FINAL_REPORT        = os.path.join(config.RESULTS_DIR, "final_report.txt")

for _d in (
    config.GENERATED_TESTS_DIR,
    config.PROMPTS_DIR,
    config.RESPONSES_DIR,
    config.PLANS_DIR,
    config.RESULTS_DIR,
):
    os.makedirs(_d, exist_ok=True)


# --- Run pipeline_step3_simple with the overridden config ------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline Step 3 simple (retargets paths via config_testable_simple)."
    )
    parser.add_argument(
        "--skip-file",
        metavar="PATH",
        help="JSON file containing unique_keys to skip.",
    )
    args = parser.parse_args()

    skip_set = set()
    if args.skip_file:
        with open(args.skip_file, encoding="utf-8") as fh:
            skip_set = set(json.load(fh))
        print(f"Loaded {len(skip_set)} keys to skip from {args.skip_file}")

    print("=" * 64)
    print("Running pipeline_step3_simple against simple-bucket artifacts:")
    print(f"  INPUT_JSON              : {config.INPUT_JSON}")
    print(f"  CLASS_INVENTORY_FILE    : {config.CLASS_INVENTORY_FILE}")
    print(f"  GENERATED_TESTS_DIR     : {config.GENERATED_TESTS_DIR}")
    print(f"  RESULTS_DIR             : {config.RESULTS_DIR}")
    print("=" * 64)

    # Imported here so its module-level imports of `config` see the overrides.
    import pipeline_step3_simple
    pipeline_step3_simple.run_pipeline(skip_set=skip_set)


if __name__ == "__main__":
    main()
