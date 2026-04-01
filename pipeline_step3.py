import os
from collections import Counter
from datetime import datetime
import config
from src.loader import load_methods
from src.prompt_builder import build_base_prompt, build_retry_prompt
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import (save_prompt, save_response,
                               save_test_file,
                               get_test_class_name)
from src.maven_runner import compile_and_run
from src.result_tracker import (load_results, save_result,
                                 is_already_processed)
from src.reporter import print_progress, print_final_report

TEST_SUITE_PATH = os.path.join(config.PDFBOX_DIR, 'src', 'test', 'java')


def get_developer_tests_for_method(method, test_suite_path):
    """
    Walks test_suite_path recursively, finds all .java test files whose
    filename contains the method's class_name, and returns their contents
    as a list of strings. Returns an empty list if none are found.
    """
    class_name = method['class_name']
    matches = []
    if not os.path.isdir(test_suite_path):
        return matches
    for root, _, files in os.walk(test_suite_path):
        for filename in files:
            if filename.endswith('.java') and class_name in filename:
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        matches.append(f.read())
                except (OSError, UnicodeDecodeError):
                    pass
    return matches


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

        # ---- BASE PROMPT ----
        developer_tests = get_developer_tests_for_method(method, TEST_SUITE_PATH)
        prompt   = build_base_prompt(method, developer_tests=developer_tests)
        response = call_llm(prompt)

        save_prompt(config.PROMPTS_DIR, unique_key, prompt)
        save_response(config.RESPONSES_DIR, unique_key, response)

        if not response:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':          'API_ERROR',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'No response from LLM'
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
                developer_tests=get_developer_tests_for_method(method, TEST_SUITE_PATH)
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
            if retry_java:
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
            else:
                retry_succeeded = False
                break

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