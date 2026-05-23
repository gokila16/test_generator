"""
pipeline_step3_simple.py

Streamlined test generation for the `simple` bucket: plan → generate →
compile/run → at most one retry. No recovery prompt, no separate allowlist
LLM round-trip, no second retry. Reuses the same maven_runner / file_manager
machinery as pipeline_step3.py — only the orchestration loop and the prompt
builders are different.

Why fewer steps work here:
  - Simple methods (cyclomatic ≤ 2, params ≤ 2, LOC < 20) are mostly getters,
    setters, trivial delegations, and short constructors. Recovery and the
    allowlist round-trip exist mainly for the harder buckets where dependency
    chains are deep and bodies long.
  - Anti-hallucination is enforced via the prompt's explicit allowlist
    (see prompt_builder_simple.py) rather than via a separate validation pass.

Inputs (set by config_testable_simple.py at runtime, not here):
  - config.INPUT_JSON              → extracted_metadata_simple.json
  - config.CLASS_INVENTORY_FILE    → class_inventory_testable.json (or .json)
  - config.GENERATED_TESTS_DIR     → pdfbox/generated_tests_simple/
  - config.RESULTS_DIR             → PDFBOX-v5/simple/results/

Run with:
    python config_testable_simple.py
"""

import argparse
import json
from collections import Counter
from datetime import datetime

import config
from src.loader import load_methods
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import (save_prompt, save_response, save_plan,
                              save_test_file, get_test_class_name,
                              get_package_for_method)
from src.maven_runner import compile_and_run
from src.result_tracker import (load_results, save_result,
                                is_already_processed)
from src.reporter import print_progress, print_final_report
from src.context_loader import load_class_inventory
from prompt_builder_simple import (build_simple_plan_prompt,
                                   build_simple_generation_prompt,
                                   build_simple_retry_prompt)
from pipeline_step3 import assign_unique_keys


def run_pipeline(skip_set=None):
    if skip_set is None:
        skip_set = set()
    start = datetime.now()
    print("=" * 60)
    print("PIPELINE STEP 3 SIMPLE: plan -> generate -> compile -> retry?")
    print("=" * 60)

    class_inventory = load_class_inventory(config.CLASS_INVENTORY_FILE)
    methods = assign_unique_keys(load_methods(config.INPUT_JSON))

    if skip_set:
        remaining = [m for m in methods if m["unique_key"] not in skip_set]
        print(f"Skip-file mode: protecting {len(skip_set)} keys.")
    else:
        remaining = [
            m for m in methods
            if not is_already_processed(config.RESULTS_JSON, m["unique_key"])
        ]
    print(f"Remaining to process: {len(remaining)}\n")

    for i, method in enumerate(remaining):
        full_name      = method["full_name"]
        unique_key     = method["unique_key"]
        class_name     = method["class_name"]
        method_name    = method["method_name"]
        overload_index = method["overload_index"]

        test_class      = get_test_class_name(class_name, method_name, overload_index)
        base_test_class = get_test_class_name(class_name, method_name)
        package         = get_package_for_method(full_name, class_inventory)

        # Augment method dict with computed fields the prompt builders need.
        method["_test_class_name"] = base_test_class
        method["_package"]         = package

        print(f"\n[{i+1}/{len(remaining)}] {method_name}")

        # ---- Step 1: PLAN ----
        plan_prompt = build_simple_plan_prompt(method)
        plan_response, _ = call_llm(plan_prompt, method_name=method_name)
        save_prompt(config.PROMPTS_DIR, unique_key, plan_prompt, is_plan=True)
        save_plan(config.PLANS_DIR, unique_key, plan_response)

        if not plan_response:
            save_result(config.RESULTS_JSON, unique_key, {
                "status":          "API_ERROR",
                "retry_triggered": False,
                "retry_succeeded": None,
                "error_message":   "No LLM response on plan step",
            })
            continue

        # ---- Step 2: GENERATE ----
        gen_prompt = build_simple_generation_prompt(method, plan_response, class_inventory)
        response, _ = call_llm(gen_prompt, method_name=method_name)
        save_prompt(config.PROMPTS_DIR, unique_key, gen_prompt)
        save_response(config.RESPONSES_DIR, unique_key, response)

        if not response:
            save_result(config.RESULTS_JSON, unique_key, {
                "status":          "API_ERROR",
                "retry_triggered": False,
                "retry_succeeded": None,
                "error_message":   "No LLM response on generation step",
            })
            continue

        java_code, fail_reason = extract_java_code(response)
        if not java_code:
            save_result(config.RESULTS_JSON, unique_key, {
                "status":                 "EXTRACTION_FAILED",
                "extraction_fail_reason": fail_reason,
                "retry_triggered":        False,
                "retry_succeeded":        None,
                "error_message":          "Could not extract Java code",
            })
            continue

        # Rename SUT-base test class to overload-indexed class for overloads.
        if overload_index is not None:
            java_code = java_code.replace(base_test_class, test_class)

        # ---- Step 3: COMPILE + RUN ----
        test_path = save_test_file(
            config.GENERATED_TESTS_DIR, full_name, class_name, method_name,
            java_code, overload_index, class_inventory=class_inventory,
        )
        compiled, passed, error = compile_and_run(
            test_path, full_name, class_name, method_name, overload_index,
            class_inventory=class_inventory,
        )

        # ---- Step 4: ONE RETRY ON FAILURE ----
        retry_triggered = False
        retry_succeeded = None
        if not (compiled and passed):
            retry_triggered = True
            print(f"  {'Compile failed' if not compiled else 'Test failed'}. Retrying once...")

            retry_prompt = build_simple_retry_prompt(method, java_code, error, class_inventory)
            retry_response, _ = call_llm(retry_prompt, method_name=method_name)
            save_prompt(config.PROMPTS_DIR, unique_key, retry_prompt, is_retry=True)
            save_response(config.RESPONSES_DIR, unique_key, retry_response, is_retry=True)

            if retry_response:
                retry_java, _ = extract_java_code(retry_response)
                if retry_java:
                    if overload_index is not None:
                        retry_java = retry_java.replace(base_test_class, test_class)
                    java_code = retry_java
                    test_path = save_test_file(
                        config.GENERATED_TESTS_DIR, full_name, class_name,
                        method_name, java_code, overload_index,
                        class_inventory=class_inventory,
                    )
                    compiled, passed, error = compile_and_run(
                        test_path, full_name, class_name, method_name,
                        overload_index, class_inventory=class_inventory,
                    )
                    retry_succeeded = compiled and passed
                else:
                    retry_succeeded = False
            else:
                retry_succeeded = False

        # ---- SAVE RESULT ----
        if not compiled:
            status = "COMPILE_FAILED"
        elif not passed:
            status = "FAILED"
        else:
            status = "PASSED"

        save_result(config.RESULTS_JSON, unique_key, {
            "status":          status,
            "retry_triggered": retry_triggered,
            "retry_succeeded": retry_succeeded,
            "retry_count":     1 if retry_triggered else 0,
            "error_message":   error,
            "test_file":       test_path,
        })

        if (i + 1) % 10 == 0:
            all_results = load_results(config.RESULTS_JSON)
            print_progress(i + 1, len(remaining), all_results)

    all_results = load_results(config.RESULTS_JSON)
    print_final_report(all_results, config.FINAL_REPORT, start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Step 3 (simple bucket)")
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
    run_pipeline(skip_set=skip_set)
