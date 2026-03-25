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


def run_pipeline():
    start_time = datetime.now()
    print("=" * 40)
    print("PIPELINE STEP 3: TEST GENERATION")
    print("=" * 40)

    # Load methods
    methods = load_methods(config.INPUT_JSON)

    # Skip already processed
    existing = load_results(config.RESULTS_JSON)
    remaining = [
        m for m in methods
        if not is_already_processed(
            config.RESULTS_JSON, m['full_name']
        )
    ]
    print(f"Already done: {len(existing)}")
    print(f"Remaining:    {len(remaining)}")

    for i, method in enumerate(remaining):
        full_name   = method['full_name']
        class_name  = method['class_name']
        method_name = method['method_name']
        test_class  = get_test_class_name(class_name)

        print(f"\n[{i+1}/{len(remaining)}] {method_name}")

        # ---- BASE PROMPT ----
        prompt   = build_base_prompt(method)
        response = call_llm(prompt)

        save_prompt(config.PROMPTS_DIR, full_name, prompt)
        save_response(config.RESPONSES_DIR, full_name, response)

        if not response:
            save_result(config.RESULTS_JSON, full_name, {
                'status':          'API_ERROR',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'No response from LLM'
            })
            continue

        java_code = extract_java_code(response)
        if not java_code:
            save_result(config.RESULTS_JSON, full_name, {
                'status':          'EXTRACTION_FAILED',
                'retry_triggered': False,
                'retry_succeeded': None,
                'error_message':   'Could not extract Java code'
            })
            continue

        # Save test file to generated_tests/
        test_path = save_test_file(
            config.GENERATED_TESTS_DIR, class_name, java_code
        )

        # ---- COMPILE AND RUN ----
        compiled, passed, error = compile_and_run(
            test_path, full_name, class_name
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

            retry_prompt = build_retry_prompt(error, java_code, method)
            retry_response = call_llm(retry_prompt)

            save_prompt(config.PROMPTS_DIR, full_name,
                       retry_prompt, is_retry=True)
            save_response(config.RESPONSES_DIR, full_name,
                         retry_response, is_retry=True)

            if not retry_response:
                save_result(config.RESULTS_JSON, full_name, {
                    'status':          'API_ERROR',
                    'retry_triggered': retry_triggered,
                    'retry_succeeded': False,
                    'retry_count':     retry_count,
                    'error_message':   'No response from LLM on retry'
                })
                break

            retry_java = extract_java_code(retry_response)
            if retry_java:
                java_code = retry_java
                test_path = save_test_file(
                    config.GENERATED_TESTS_DIR, class_name, retry_java
                )
                compiled, passed, error = compile_and_run(
                    test_path, full_name, class_name
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

        save_result(config.RESULTS_JSON, full_name, {
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