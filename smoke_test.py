"""Limited smoke run of the test-generation pipeline.

Runs the SAME per-method logic as pipeline_step3 but only on the first N
not-yet-processed methods, so you can verify generate -> compile -> run end to end
before launching the full 1309. Results are written to the real results.json, so
processed methods are skipped on the full run.

Usage:  python smoke_test.py [N]   (default N=2)
"""
import sys
import config
from src.loader import load_methods
from src.prompt_builder import build_base_prompt, build_retry_prompt
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import save_prompt, save_response, save_test_file, get_test_class_name
from src.maven_runner import compile_and_run
from src.result_tracker import load_results, save_result, is_already_processed
from pipeline_step3 import assign_unique_keys


def main(limit):
    methods = assign_unique_keys(load_methods(config.INPUT_JSON))
    remaining = [m for m in methods
                 if not is_already_processed(config.RESULTS_JSON, m['unique_key'])]
    batch = remaining[:limit]
    print(f"\nSMOKE RUN: processing {len(batch)} of {len(remaining)} remaining methods\n")

    for i, method in enumerate(batch):
        full_name      = method['full_name']
        unique_key     = method['unique_key']
        class_name     = method['class_name']
        method_name    = method['method_name']
        overload_index = method['overload_index']

        print(f"[{i+1}/{len(batch)}] {unique_key}")

        prompt   = build_base_prompt(method)
        response = call_llm(prompt)
        save_prompt(config.PROMPTS_DIR, unique_key, prompt)
        save_response(config.RESPONSES_DIR, unique_key, response)

        if not response:
            print("    -> API_ERROR (no response)\n")
            save_result(config.RESULTS_JSON, unique_key,
                        {'status': 'API_ERROR', 'error_message': 'No response from LLM'})
            continue

        java_code = extract_java_code(response)
        if not java_code:
            print("    -> EXTRACTION_FAILED\n")
            save_result(config.RESULTS_JSON, unique_key,
                        {'status': 'EXTRACTION_FAILED', 'error_message': 'Could not extract Java code'})
            continue

        if overload_index is not None:
            base_class  = get_test_class_name(class_name, method_name)
            index_class = get_test_class_name(class_name, method_name, overload_index)
            java_code = java_code.replace(base_class, index_class)

        test_path = save_test_file(config.GENERATED_TESTS_DIR, full_name,
                                   class_name, method_name, java_code, overload_index)
        compiled, passed, error = compile_and_run(test_path, full_name, class_name,
                                                  method_name, overload_index)

        retry_count = 0
        while (not compiled or not passed) and retry_count < config.MAX_RETRIES:
            retry_count += 1
            reason = 'Compile failed' if not compiled else 'Test failed'
            print(f"    {reason}. Retry {retry_count}/{config.MAX_RETRIES}...")
            retry_prompt = build_retry_prompt(error, java_code, method)
            retry_response = call_llm(retry_prompt)
            save_prompt(config.PROMPTS_DIR, full_name, retry_prompt, is_retry=True)
            save_response(config.RESPONSES_DIR, full_name, retry_response, is_retry=True)
            if not retry_response:
                break
            retry_java = extract_java_code(retry_response)
            if not retry_java:
                break
            if overload_index is not None:
                retry_java = retry_java.replace(get_test_class_name(class_name, method_name),
                                                get_test_class_name(class_name, method_name, overload_index))
            java_code = retry_java
            test_path = save_test_file(config.GENERATED_TESTS_DIR, full_name,
                                       class_name, method_name, retry_java, overload_index)
            compiled, passed, error = compile_and_run(test_path, full_name, class_name,
                                                      method_name, overload_index)

        status = 'PASSED' if (compiled and passed) else ('COMPILE_FAILED' if not compiled else 'FAILED')
        print(f"    -> {status} (retries: {retry_count})")
        if status != 'PASSED' and error:
            print("    error (first 500 chars):")
            print("    " + (error[:500].replace('\n', '\n    ')))
        print()
        save_result(config.RESULTS_JSON, unique_key,
                    {'status': status, 'retry_count': retry_count,
                     'error_message': error, 'test_file': test_path})


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    main(n)
