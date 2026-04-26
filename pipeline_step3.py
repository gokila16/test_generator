import argparse
import json
import os
from collections import Counter
from datetime import datetime
import config
from src.loader import load_methods
from src.prompt_builder import (build_planning_prompt, build_generation_from_plan_prompt,
                                build_retry_prompt, build_allowlist_violation_prompt,
                                build_recovery_prompt)
from src.allowlist_checker import check_against_allowlist, validate_imports
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import (save_prompt, save_response, save_plan,
                               save_test_file, get_test_class_name)
from src.maven_runner import compile_and_run
from src.result_tracker import (load_results, save_result,
                                 is_already_processed)
from src.reporter import print_progress, print_final_report
from src.context_loader import load_context_data, load_class_inventory, get_dependency_chain, get_caller_snippets
from src.java_post_processor import post_process_java, apply_java_fixes

def _extract_and_process(response_text, class_inventory=None, **pp_kwargs):
    """
    Chains extract_java_code → post_process_java.

    Returns a 3-tuple (java_code, raw_extracted, fail_reason):
      - java_code:      fully fixed + validated code, or None on failure
      - raw_extracted:  code from extract_java_code before post-processing
                        (None if extraction itself failed); used by Tier 3
                        to bypass validation and route straight to Maven
      - fail_reason:    None on success, otherwise the failing layer's reason
    """
    raw, extract_reason = extract_java_code(response_text)
    if raw is None:
        return None, None, extract_reason
    code, pp_reason = post_process_java(raw, class_inventory=class_inventory, **pp_kwargs)
    return code, raw, pp_reason


# Tier classification for extraction failures.
#
# Tier 1 — no usable Java at all: send recovery prompt (with plan)
# Tier 2 — Java exists but Maven won't surface the problem: send recovery prompt
# Tier 3 — Java exists and Maven will produce a compile error: skip recovery,
#           apply fixes to raw code and hand straight to Maven/retry loop
_TIER1 = {'no_code_block', 'no_test_annotation'}
_TIER2 = {'sut_missing', 'trivial_assertions', 'no_test_methods'}
_TIER3 = {'truncated', 'mockito_import', 'class_redefinition', 'nested_class_redefinition'}


def _classify_fail_reason(reason):
    """Returns 1, 2, or 3 for the given extraction/post-process fail reason."""
    if reason in _TIER1:
        return 1
    if reason in _TIER2:
        return 2
    # constructor_arity messages start with 'constructor_arity:'
    if reason in _TIER3 or (reason and reason.startswith('constructor_arity:')):
        return 3
    return 1  # unknown reasons default to recovery


def assign_unique_keys(methods):
    """Assign a unique results.json key and overload index to each method.
    Overloaded methods (same full_name) get a numeric suffix: full_name_0, full_name_1, ...
    """
    counts = Counter(m['full_name'] for m in methods)
    seen = Counter()
    for m in methods:
        fn = m['full_name']
        if counts[fn] > 1:
            m['unique_key'] = f"{fn}_overload_{seen[fn]}"
            m['overload_index'] = seen[fn]
        else:
            m['unique_key'] = fn
            m['overload_index'] = None
        seen[fn] += 1
    return methods


_COMPARISON_CATEGORIES = [
    'EXTRACTION_FAILED',
    'ALLOWLIST_FAILED',
    'COMPILE_FAILED',
    'FAILED',
    'PASSED',
]

# (direction, threshold) — direction is 'below' or 'above'
_THRESHOLDS = {
    'EXTRACTION_FAILED': ('below',  80),
    'ALLOWLIST_FAILED':  ('below',  50),
    'COMPILE_FAILED':    ('below', 180),
    'PASSED':            ('above', 750),
}


def _print_comparison_table(before_counts, after_results):
    after_counts = Counter(v.get('status', 'UNKNOWN') for v in after_results.values())

    print("\n" + "=" * 58)
    print("BEFORE / AFTER COMPARISON")
    print("=" * 58)
    print(f"  {'Category':<24} {'Before':>6} {'After':>6} {'Delta':>6}")
    print("-" * 58)
    for cat in _COMPARISON_CATEGORIES:
        before    = before_counts.get(cat, 0)
        after     = after_counts.get(cat, 0)
        delta     = after - before
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        print(f"  {cat:<24} {before:>6} {after:>6} {delta_str:>6}")
    print("=" * 58)

    # extraction_fail_reason breakdown
    fail_reasons = {}
    for r in after_results.values():
        if r.get('status') == 'EXTRACTION_FAILED':
            reason = r.get('extraction_fail_reason') or 'unknown'
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
    if fail_reasons:
        print("\nExtraction fail reasons (after):")
        for reason, count in sorted(fail_reasons.items()):
            print(f"  {reason:<30} {count}")

    # Target threshold check
    print("\nTarget thresholds:")
    all_met = True
    for cat, (direction, threshold) in _THRESHOLDS.items():
        count = after_counts.get(cat, 0)
        met   = (count < threshold) if direction == 'below' else (count > threshold)
        mark  = 'PASS' if met else 'FAIL'
        print(f"  [{mark}] {cat} {direction} {threshold}: got {count}")
        if not met:
            all_met = False
    print("\n" + ("All targets met." if all_met else "One or more targets NOT met."))
    print("=" * 58)


def run_pipeline(skip_set=None):
    if skip_set is None:
        skip_set = set()
    start_time = datetime.now()
    truncation_count = 0
    print("=" * 40)
    print("PIPELINE STEP 3: TEST GENERATION")
    print("=" * 40)

    # Load context data once at startup
    dep_chains, call_graph = load_context_data(
        config.DEPENDENCY_CHAINS_FILE, config.CALL_GRAPH_FILE
    )
    class_inventory = load_class_inventory(config.CLASS_INVENTORY_FILE)

    # Load methods and assign unique keys for overloads
    methods = assign_unique_keys(load_methods(config.INPUT_JSON))

    # Snapshot before-counts for the comparison table printed at the end
    existing      = load_results(config.RESULTS_JSON)
    before_counts = Counter(v.get('status', 'UNKNOWN') for v in existing.values())

    if skip_set:
        # Re-run every method that is NOT in the skip list, even if it was
        # previously processed.  Methods in skip_set keep their existing results.
        remaining = [m for m in methods if m['unique_key'] not in skip_set]
        print(f"Skip-file mode: protecting {len(skip_set)} PASSED baseline methods")
        print(f"Remaining (to re-run): {len(remaining)}")
    else:
        # Default behaviour: skip anything already recorded in results.json
        remaining = [
            m for m in methods
            if not is_already_processed(config.RESULTS_JSON, m['unique_key'])
        ]
        print(f"Already done: {len(existing)}")
        print(f"Remaining:    {len(remaining)}")

    for i, method in enumerate(remaining):
        full_name      = method['full_name']
        unique_key     = method['unique_key']
        class_name     = method['class_name']
        method_name    = method['method_name']
        overload_index = method['overload_index']
        test_class      = get_test_class_name(class_name, method_name, overload_index)
        base_test_class = get_test_class_name(class_name, method_name)   # what the LLM always generates
        package         = '.'.join(full_name.split('.')[:-2])

        print(f"\n[{i+1}/{len(remaining)}] {method_name}")

        # ---- STEP 1: PLANNING ----
        dep_chain       = get_dependency_chain(dep_chains, method)
        caller_snippets = get_caller_snippets(call_graph, method, max_snippets=2)
        plan_prompt     = build_planning_prompt(method, dep_chain=dep_chain, caller_snippets=caller_snippets, class_inventory=class_inventory)
        plan_response, _trunc = call_llm(plan_prompt, method_name=method_name)
        if _trunc:
            truncation_count += 1

        save_prompt(config.PROMPTS_DIR, unique_key, plan_prompt, is_plan=True)
        save_plan(config.PLANS_DIR, unique_key, plan_response)

        if not plan_response:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':          'API_ERROR',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'No response from LLM on planning step'
            })
            continue

        # ---- STEP 2: GENERATION FROM PLAN ----
        gen_prompt = build_generation_from_plan_prompt(method, plan_response, dep_chain=dep_chain)
        response, _trunc = call_llm(gen_prompt, method_name=method_name)
        if _trunc:
            truncation_count += 1

        save_prompt(config.PROMPTS_DIR, unique_key, gen_prompt)
        save_response(config.RESPONSES_DIR, unique_key, response)

        if not response:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':          'API_ERROR',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'No response from LLM on generation step'
            })
            continue

        java_code, raw_code, fail_reason = _extract_and_process(response, class_inventory=class_inventory, expected_package=package, test_class_name=base_test_class, sut_class_name=class_name)
        if not java_code:
            tier = _classify_fail_reason(fail_reason)
            print(f"  Extraction failed ({fail_reason}) [Tier {tier}].")

            if tier == 3 and raw_code is not None:
                # ---- TIER 3: bypass recovery, apply fixes, hand to Maven/retry ----
                print(f"  Routing directly to Maven for compile-error recovery.")
                java_code = apply_java_fixes(raw_code, expected_package=package)
            else:
                # ---- TIER 1 / 2: targeted recovery prompt anchored on the plan ----
                print(f"  Attempting recovery prompt...")
                recovery_prompt = build_recovery_prompt(
                    fail_reason, response, method, plan_response
                )
                recovery_response, _trunc = call_llm(recovery_prompt, method_name=method_name)
                if _trunc:
                    truncation_count += 1
                save_prompt(config.PROMPTS_DIR, unique_key, recovery_prompt, is_recovery=True)
                save_response(config.RESPONSES_DIR, unique_key, recovery_response, is_allowlist=False)

                if recovery_response:
                    java_code, raw_code, fail_reason = _extract_and_process(
                        recovery_response,
                        class_inventory=class_inventory,
                        expected_package=package,
                        test_class_name=base_test_class,
                        sut_class_name=class_name,
                    )
                    # If recovery itself produced a Tier 3 failure, still try Maven
                    if not java_code and _classify_fail_reason(fail_reason) == 3 and raw_code is not None:
                        print(f"  Recovery also Tier 3 ({fail_reason}). Routing to Maven.")
                        java_code = apply_java_fixes(raw_code, expected_package=package)

            if not java_code:
                save_result(config.RESULTS_JSON, unique_key, {
                    'status':                  'EXTRACTION_FAILED',
                    'extraction_fail_reason':  fail_reason,
                    'retry_triggered':         False,
                    'retry_succeeded':         None,
                    'error_message':           'Could not extract Java code'
                })
                continue

        # ---- STATIC ALLOWLIST CHECK ----
        # Verify the generated test only calls methods present in dependency_signatures.
        # This runs before Maven to catch hallucinated method names early.
        # Uses its own retry budget (MAX_ALLOWLIST_RETRIES), separate from Maven retries.
        MAX_ALLOWLIST_RETRIES = 2
        allowlist_retry_count = 0
        allowlist_violations = []

        while True:
            import_violations = validate_imports(
                java_code=java_code,
                source_file_imports=method.get('source_file_imports', []),
                class_inventory=class_inventory,
            )
            if import_violations:
                allowlist_passed = False
                allowlist_violations = import_violations
                print(f"  Import validation failed. Hallucinated imports: {allowlist_violations}")
            else:
                allowlist_passed, allowlist_violations = check_against_allowlist(java_code, method, class_inventory)
                if not allowlist_passed:
                    print(f"  Allowlist check failed. Hallucinated methods: {allowlist_violations}")
            if allowlist_passed:
                break

            if allowlist_retry_count >= MAX_ALLOWLIST_RETRIES:
                print(f"  Allowlist retries exhausted ({MAX_ALLOWLIST_RETRIES}). Skipping Maven.")
                break

            allowlist_retry_count += 1
            print(f"  Allowlist retry {allowlist_retry_count}/{MAX_ALLOWLIST_RETRIES}...")

            violation_prompt = build_allowlist_violation_prompt(
                violations=allowlist_violations,
                generated_test=java_code,
                method=method,
                dep_chain=dep_chain,
                class_inventory=class_inventory,
            )
            violation_response, _trunc = call_llm(violation_prompt, method_name=method_name)
            if _trunc:
                truncation_count += 1

            save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
            save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

            if not violation_response:
                print("  No LLM response on allowlist retry.")
                break

            new_java, _, _ = _extract_and_process(violation_response, class_inventory=class_inventory, expected_package=package, test_class_name=base_test_class, sut_class_name=class_name)
            if new_java:
                java_code = new_java
            else:
                print("  Could not extract Java code on allowlist retry.")
                break

        if not allowlist_passed:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':               'ALLOWLIST_FAILED',
                'allowlist_violations': allowlist_violations,
                'allowlist_retry_count': allowlist_retry_count,
                'retry_triggered':      False,
                'retry_succeeded':      None,
                'error_message':        f"Hallucinated methods: {', '.join(allowlist_violations)}"
            })
            continue

        # For overloads, rename the class in the generated code to match the indexed filename
        if overload_index is not None:
            base_class  = get_test_class_name(class_name, method_name)
            index_class = get_test_class_name(class_name, method_name, overload_index)
            java_code = java_code.replace(base_class, index_class)

        # Save test file
        test_path = save_test_file(config.GENERATED_TESTS_DIR, full_name, class_name, method_name, java_code, overload_index)

        # Compile and run
        compiled, passed, error = compile_and_run(
            test_path, full_name, class_name, method_name, overload_index
        )

        # ---- RETRY UP TO 2 TIMES IF FAILED ----
        retry_triggered = False
        retry_succeeded = None
        max_retries     = config.MAX_RETRIES
        retry_count     = 0

        while (not compiled or not passed) and retry_count < max_retries:
            retry_triggered = True
            retry_count    += 1
            reason = 'Compile failed' if not compiled else 'Test failed'
            print(f"  {reason}. Retry {retry_count}/{max_retries}...")

            retry_prompt = build_retry_prompt(
                error_message=error,
                failing_test=java_code,
                method=method,
                class_inventory=class_inventory,
                dep_chain=dep_chain,
            )
            retry_response, _trunc = call_llm(retry_prompt, method_name=method_name)
            if _trunc:
                truncation_count += 1

            save_prompt(config.PROMPTS_DIR, full_name,
                       retry_prompt, is_retry=True)
            save_response(config.RESPONSES_DIR, full_name,
                         retry_response, is_retry=True)

            if not retry_response:
                save_result(config.RESULTS_JSON, unique_key, {
                    'status':          'API_ERROR',
                    'retry_triggered': retry_triggered,
                    'retry_succeeded': False,
                    'retry_count':     retry_count,
                    'error_message':   'No response from LLM on retry'
                })
                break

            retry_java, _, _ = _extract_and_process(retry_response, class_inventory=class_inventory, expected_package=package, test_class_name=base_test_class, sut_class_name=class_name)
            if not retry_java:
                retry_succeeded = False
                break

            # Allowlist check on retry-generated code before running Maven
            retry_allowlist_retry_count = 0
            retry_allowlist_violations = []

            while True:
                retry_allowlist_passed, retry_allowlist_violations = check_against_allowlist(retry_java, method, class_inventory)
                if retry_allowlist_passed:
                    break

                print(f"  Allowlist check failed on retry. Hallucinated methods: {retry_allowlist_violations}")

                if retry_allowlist_retry_count >= MAX_ALLOWLIST_RETRIES:
                    print(f"  Allowlist retries exhausted on retry. Skipping Maven.")
                    break

                retry_allowlist_retry_count += 1
                print(f"  Allowlist retry {retry_allowlist_retry_count}/{MAX_ALLOWLIST_RETRIES}...")

                violation_prompt = build_allowlist_violation_prompt(
                    violations=retry_allowlist_violations,
                    generated_test=retry_java,
                    method=method,
                    dep_chain=dep_chain,
                    class_inventory=class_inventory,
                )
                violation_response, _trunc = call_llm(violation_prompt, method_name=method_name)
                if _trunc:
                    truncation_count += 1

                save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
                save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

                if not violation_response:
                    print("  No LLM response on allowlist retry.")
                    break

                new_java, _, _ = _extract_and_process(violation_response, class_inventory=class_inventory, expected_package=package, test_class_name=base_test_class, sut_class_name=class_name)
                if new_java:
                    retry_java = new_java
                else:
                    print("  Could not extract Java code on allowlist retry.")
                    break

            if not retry_allowlist_passed:
                save_result(config.RESULTS_JSON, unique_key, {
                    'status':                    'ALLOWLIST_FAILED_ON_RETRY',
                    'allowlist_violations':      retry_allowlist_violations,
                    'allowlist_retry_count':     retry_allowlist_retry_count,
                    'retry_triggered':           retry_triggered,
                    'retry_succeeded':           False,
                    'retry_count':               retry_count,
                    'error_message':             f"Hallucinated methods on retry: {', '.join(retry_allowlist_violations)}"
                })
                break

            if overload_index is not None:
                base_class  = get_test_class_name(class_name, method_name)
                index_class = get_test_class_name(class_name, method_name, overload_index)
                retry_java = retry_java.replace(base_class, index_class)
            java_code = retry_java
            test_path = save_test_file(
                config.GENERATED_TESTS_DIR, full_name, class_name, method_name, retry_java, overload_index
            )
            compiled, passed, error = compile_and_run(
                test_path, full_name, class_name, method_name, overload_index
            )
            retry_succeeded = compiled and passed

        # ---- SAVE RESULT ----
        if not compiled:
            status = 'COMPILE_FAILED'
        elif not passed:
            status = 'FAILED'
        else:
            status = 'PASSED'

        save_result(config.RESULTS_JSON, unique_key, {
            'status':          status,
            'retry_triggered': retry_triggered,
            'retry_succeeded': retry_succeeded,
            'retry_count':     retry_count,
            'error_message':   error,
            'test_file':       test_path
        })

        # Progress every 10 methods
        if (i + 1) % 10 == 0:
            all_results = load_results(config.RESULTS_JSON)
            print_progress(i + 1, len(remaining), all_results)

    # Final report
    all_results = load_results(config.RESULTS_JSON)
    print_final_report(all_results, config.FINAL_REPORT, start_time)

    if skip_set:
        # Only meaningful when a skip-file was supplied (otherwise before == after
        # for already-processed methods and the table would be misleading).
        _print_comparison_table(before_counts, all_results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pipeline Step 3: Test Generation')
    parser.add_argument(
        '--skip-file',
        metavar='PATH',
        help='JSON file containing unique_keys to skip (preserve existing PASSED results)',
    )
    args = parser.parse_args()

    skip_set = set()
    if args.skip_file:
        with open(args.skip_file, encoding='utf-8') as fh:
            skip_set = set(json.load(fh))
        print(f"Loaded {len(skip_set)} keys to skip from {args.skip_file}")

    run_pipeline(skip_set=skip_set)