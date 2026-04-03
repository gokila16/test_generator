import os
from collections import Counter
from datetime import datetime
import config
from src.loader import load_methods
from src.prompt_builder import (build_planning_prompt, build_generation_from_plan_prompt,
                                build_retry_prompt, build_allowlist_violation_prompt)
from src.allowlist_checker import check_against_allowlist
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import (save_prompt, save_response, save_plan,
                               save_test_file, get_test_class_name)
from src.maven_runner import compile_and_run
from src.result_tracker import (load_results, save_result,
                                 is_already_processed)
from src.reporter import print_progress, print_final_report

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


def run_pipeline():
    start_time = datetime.now()
    print("=" * 40)
    print("PIPELINE STEP 3: TEST GENERATION")
    print("=" * 40)

    # Load methods and assign unique keys for overloads
    methods = assign_unique_keys(load_methods(config.INPUT_JSON))

    # Skip already processed
    existing = load_results(config.RESULTS_JSON)
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
        test_class     = get_test_class_name(class_name, method_name, overload_index)

        print(f"\n[{i+1}/{len(remaining)}] {method_name}")

        # ---- STEP 1: PLANNING ----
        plan_prompt    = build_planning_prompt(method)
        plan_response  = call_llm(plan_prompt)

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
        gen_prompt = build_generation_from_plan_prompt(method, plan_response)
        response   = call_llm(gen_prompt)

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

        java_code = extract_java_code(response)
        if not java_code:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':          'EXTRACTION_FAILED',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'Could not extract Java code'
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
            allowlist_passed, allowlist_violations = check_against_allowlist(java_code, method)
            if allowlist_passed:
                break

            print(f"  Allowlist check failed. Hallucinated methods: {allowlist_violations}")

            if allowlist_retry_count >= MAX_ALLOWLIST_RETRIES:
                print(f"  Allowlist retries exhausted ({MAX_ALLOWLIST_RETRIES}). Skipping Maven.")
                break

            allowlist_retry_count += 1
            print(f"  Allowlist retry {allowlist_retry_count}/{MAX_ALLOWLIST_RETRIES}...")

            violation_prompt = build_allowlist_violation_prompt(
                violations=allowlist_violations,
                generated_test=java_code,
                method=method
            )
            violation_response = call_llm(violation_prompt)

            save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
            save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

            if not violation_response:
                print("  No LLM response on allowlist retry.")
                break

            new_java = extract_java_code(violation_response)
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
                method=method
            )
            retry_response = call_llm(retry_prompt)

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

            retry_java = extract_java_code(retry_response)
            if not retry_java:
                retry_succeeded = False
                break

            # Allowlist check on retry-generated code before running Maven
            retry_allowlist_retry_count = 0
            retry_allowlist_violations = []

            while True:
                retry_allowlist_passed, retry_allowlist_violations = check_against_allowlist(retry_java, method)
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
                    method=method
                )
                violation_response = call_llm(violation_prompt)

                save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
                save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

                if not violation_response:
                    print("  No LLM response on allowlist retry.")
                    break

                new_java = extract_java_code(violation_response)
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


if __name__ == '__main__':
    run_pipeline()