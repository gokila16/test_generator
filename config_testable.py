"""
config_testable.py

Runtime shim that retargets pipeline_step3 at the `*_testable*` artifacts
without modifying config.py or pipeline_step3.py. It imports `config`,
overrides path constants in place, isolates outputs into a separate tree,
and then invokes pipeline_step3.run_pipeline() with the same --skip-file
argument the original entrypoint accepts.

Usage (from test_generator/):
    python config_testable.py
    python config_testable.py --skip-file <path-to-skip.json>
"""

import argparse
import json
import os

import config
import maven_runner_testable  # noqa: F401  patches src.maven_runner.subprocess


# --- Override input paths --------------------------------------------------

config.INPUT_JSON = os.path.join(
    config.GENERATOR_DIR, "extracted_metadata_testable.json"
)
config.DEPENDENCY_CHAINS_FILE = os.path.join(
    config.GENERATOR_DIR, "dependency_chains_testable.json"
)
config.CLASS_INVENTORY_FILE = os.path.join(
    config.GENERATOR_DIR, "class_inventory_testable.json"
)

# CALL_GRAPH_FILE is left untouched — context_loader silently falls back to
# an empty dict if the file is absent, so generation still runs without it.


# --- Isolate outputs into a parallel tree so the main run is not clobbered ---

TESTABLE_RUN_DIR = os.path.join(config.BASE_DIR, "testable")

config.GENERATED_TESTS_DIR = os.path.join(config.PDFBOX_DIR, "generated_tests_testable")
config.PROMPTS_DIR         = os.path.join(TESTABLE_RUN_DIR, "prompts")
config.RESPONSES_DIR       = os.path.join(TESTABLE_RUN_DIR, "responses")
config.PLANS_DIR           = os.path.join(TESTABLE_RUN_DIR, "plans")
config.RESULTS_DIR         = os.path.join(TESTABLE_RUN_DIR, "results")
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


# --- Run pipeline_step3 with the overridden config -------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline Step 3 (testable variant): retargets paths via config_testable."
    )
    parser.add_argument(
        "--skip-file",
        metavar="PATH",
        help="JSON file containing unique_keys to skip (preserve existing PASSED results).",
    )
    args = parser.parse_args()

    skip_set = set()
    if args.skip_file:
        with open(args.skip_file, encoding="utf-8") as fh:
            skip_set = set(json.load(fh))
        print(f"Loaded {len(skip_set)} keys to skip from {args.skip_file}")

    print("=" * 58)
    print("Running pipeline_step3 against testable artifacts:")
    print(f"  INPUT_JSON              : {config.INPUT_JSON}")
    print(f"  DEPENDENCY_CHAINS_FILE  : {config.DEPENDENCY_CHAINS_FILE}")
    print(f"  CLASS_INVENTORY_FILE    : {config.CLASS_INVENTORY_FILE}")
    print(f"  GENERATED_TESTS_DIR     : {config.GENERATED_TESTS_DIR}")
    print(f"  RESULTS_DIR             : {config.RESULTS_DIR}")
    print("=" * 58)

    # Imported here, after mutations, so its module-level imports of `config`
    # see the overridden values.
    import pipeline_step3
    pipeline_step3.run_pipeline(skip_set=skip_set)


if __name__ == "__main__":
    main()
