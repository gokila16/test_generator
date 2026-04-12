"""
Simple-path per-method logic.

run_simple_path() processes one method through:
  plan → generate → allowlist check → Maven (with retries)

It is called by both pipeline_step3.py (always simple) and pipeline_v5.py
(simple path for simple methods, or as fallback after resource failure).

Optional parameters:
  checklist        — stripped checklist dict injected into the planning prompt
                     as extra branch context (complex_resource_fallback path).
  resources        — validated resource constructions injected into the
                     planning prompt and assembled into the test after
                     generation (full complex path).
  resource_fallback — True when called as a fallback after resource gen failed;
                     recorded in the result's 'path' field.
"""
import config
from src.prompt_builder import (
    build_planning_prompt, build_generation_from_plan_prompt,
    build_retry_prompt, build_allowlist_violation_prompt,
)
from src.allowlist_checker import check_against_allowlist
from src.llm_client import call_llm
from src.code_extractor import extract_java_code
from src.file_manager import (
    save_prompt, save_response, save_plan,
    save_test_file, get_test_class_name, assemble_test_package,
)
from src.maven_runner import compile_and_run
from src.result_tracker import save_result
from src.context_loader import get_dependency_chain, get_caller_snippets
from src.java_post_processor import post_process_java


def run_simple_path(
    method:            dict,
    dep_chains:        dict,
    call_graph:        dict,
    checklist:         dict | None = None,
    resources:         dict | None = None,
    resource_fallback: bool        = False,
) -> str:
    """
    Processes one method and saves its result to config.RESULTS_JSON.
    Returns the final status string.

    checklist  — when provided, injected into the planning prompt as branch
                 context (only branch_plan + input_types; resource_spec is
                 stripped before passing from the complex fallback path).
    resources  — when provided, injected into the planning prompt as pre-built
                 resource context AND assembled into the generated test code
                 before saving (full complex path only).
    resource_fallback — marks result path as 'complex_resource_fallback'.
    """
    full_name      = method['full_name']
    unique_key     = method['unique_key']
    class_name     = method['class_name']
    method_name    = method['method_name']
    overload_index = method['overload_index']

    if resource_fallback:
        path = 'complex_resource_fallback'
    elif resources is not None:
        path = 'complex'
    else:
        path = 'simple'

    dep_chain       = get_dependency_chain(dep_chains, method)
    caller_snippets = get_caller_snippets(call_graph, method, max_snippets=2)

    # ------------------------------------------------------------------ #
    # STEP 1: PLANNING                                                     #
    # ------------------------------------------------------------------ #
    plan_prompt  = build_planning_prompt(
        method,
        dep_chain=dep_chain,
        caller_snippets=caller_snippets,
        checklist=checklist,
        resources=resources,
    )
    plan_response = call_llm(plan_prompt)

    save_prompt(config.PROMPTS_DIR, unique_key, plan_prompt, is_plan=True)
    save_plan(config.PLANS_DIR, unique_key, plan_response)

    if not plan_response:
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          'API_ERROR',
            'path':            path,
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   'No response from LLM on planning step',
        })
        return 'API_ERROR'

    # ------------------------------------------------------------------ #
    # STEP 2: GENERATION FROM PLAN                                         #
    # ------------------------------------------------------------------ #
    gen_prompt = build_generation_from_plan_prompt(method, plan_response)
    response   = call_llm(gen_prompt)

    save_prompt(config.PROMPTS_DIR, unique_key, gen_prompt)
    save_response(config.RESPONSES_DIR, unique_key, response)

    if not response:
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          'API_ERROR',
            'path':            path,
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   'No response from LLM on generation step',
        })
        return 'API_ERROR'

    java_code = post_process_java(extract_java_code(response))
    if not java_code:
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          'EXTRACTION_FAILED',
            'path':            path,
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   'Could not extract Java code',
        })
        return 'EXTRACTION_FAILED'

    # ------------------------------------------------------------------ #
    # STATIC ALLOWLIST CHECK                                               #
    # Runs before Maven; has its own retry budget independent of Maven.    #
    # ------------------------------------------------------------------ #
    MAX_ALLOWLIST_RETRIES = 2
    allowlist_retry_count = 0
    allowlist_passed      = False
    allowlist_violations  = []

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

        violation_prompt  = build_allowlist_violation_prompt(
            violations=allowlist_violations,
            generated_test=java_code,
            method=method,
        )
        violation_response = call_llm(violation_prompt)

        save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
        save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

        if not violation_response:
            print("  No LLM response on allowlist retry.")
            break

        new_java = post_process_java(extract_java_code(violation_response))
        if new_java:
            java_code = new_java
        else:
            print("  Could not extract Java code on allowlist retry.")
            break

    if not allowlist_passed:
        save_result(config.RESULTS_JSON, unique_key, {
            'status':                'ALLOWLIST_FAILED',
            'path':                  path,
            'allowlist_violations':  allowlist_violations,
            'allowlist_retry_count': allowlist_retry_count,
            'retry_triggered':       False,
            'retry_succeeded':       None,
            'error_message':         f"Hallucinated methods: {', '.join(allowlist_violations)}",
        })
        return 'ALLOWLIST_FAILED'

    # ------------------------------------------------------------------ #
    # OVERLOAD RENAME + RESOURCE ASSEMBLY + SAVE                          #
    # ------------------------------------------------------------------ #
    if overload_index is not None:
        base_class  = get_test_class_name(class_name, method_name)
        index_class = get_test_class_name(class_name, method_name, overload_index)
        java_code   = java_code.replace(base_class, index_class)

    # Inject pre-built resource constructions when available (complex path)
    if resources:
        java_code = assemble_test_package(java_code, resources, method, overload_index)

    test_path = save_test_file(
        config.GENERATED_TESTS_DIR, full_name, class_name, method_name,
        java_code, overload_index,
    )

    # ------------------------------------------------------------------ #
    # MAVEN: COMPILE + RUN                                                 #
    # ------------------------------------------------------------------ #
    compiled, passed, error = compile_and_run(
        test_path, full_name, class_name, method_name, overload_index,
    )

    # ------------------------------------------------------------------ #
    # MAVEN RETRY LOOP (up to config.MAX_RETRIES)                          #
    # ------------------------------------------------------------------ #
    retry_triggered = False
    retry_succeeded = None
    retry_count     = 0

    while (not compiled or not passed) and retry_count < config.MAX_RETRIES:
        retry_triggered = True
        retry_count    += 1
        reason = 'Compile failed' if not compiled else 'Test failed'
        print(f"  {reason}. Retry {retry_count}/{config.MAX_RETRIES}...")

        retry_prompt   = build_retry_prompt(
            error_message=error,
            failing_test=java_code,
            method=method,
        )
        retry_response = call_llm(retry_prompt)

        save_prompt(config.PROMPTS_DIR, unique_key, retry_prompt, is_retry=True)
        save_response(config.RESPONSES_DIR, unique_key, retry_response, is_retry=True)

        if not retry_response:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':          'API_ERROR',
                'path':            path,
                'retry_triggered': retry_triggered,
                'retry_succeeded': False,
                'retry_count':     retry_count,
                'error_message':   'No response from LLM on retry',
            })
            return 'API_ERROR'

        retry_java = post_process_java(extract_java_code(retry_response))
        if not retry_java:
            retry_succeeded = False
            break

        # Allowlist check on retry-generated code before running Maven
        retry_allowlist_retry_count = 0
        retry_allowlist_passed      = False
        retry_allowlist_violations  = []

        while True:
            retry_allowlist_passed, retry_allowlist_violations = \
                check_against_allowlist(retry_java, method)
            if retry_allowlist_passed:
                break

            print(f"  Allowlist check failed on retry. "
                  f"Hallucinated methods: {retry_allowlist_violations}")

            if retry_allowlist_retry_count >= MAX_ALLOWLIST_RETRIES:
                print("  Allowlist retries exhausted on retry. Skipping Maven.")
                break

            retry_allowlist_retry_count += 1
            print(f"  Allowlist retry {retry_allowlist_retry_count}/{MAX_ALLOWLIST_RETRIES}...")

            violation_prompt   = build_allowlist_violation_prompt(
                violations=retry_allowlist_violations,
                generated_test=retry_java,
                method=method,
            )
            violation_response = call_llm(violation_prompt)

            save_prompt(config.PROMPTS_DIR, unique_key, violation_prompt, is_allowlist=True)
            save_response(config.RESPONSES_DIR, unique_key, violation_response, is_allowlist=True)

            if not violation_response:
                print("  No LLM response on allowlist retry.")
                break

            new_java = post_process_java(extract_java_code(violation_response))
            if new_java:
                retry_java = new_java
            else:
                print("  Could not extract Java code on allowlist retry.")
                break

        if not retry_allowlist_passed:
            save_result(config.RESULTS_JSON, unique_key, {
                'status':                'ALLOWLIST_FAILED_ON_RETRY',
                'path':                  path,
                'allowlist_violations':  retry_allowlist_violations,
                'allowlist_retry_count': retry_allowlist_retry_count,
                'retry_triggered':       retry_triggered,
                'retry_succeeded':       False,
                'retry_count':           retry_count,
                'error_message':         (
                    f"Hallucinated methods on retry: "
                    f"{', '.join(retry_allowlist_violations)}"
                ),
            })
            return 'ALLOWLIST_FAILED_ON_RETRY'

        if overload_index is not None:
            base_class  = get_test_class_name(class_name, method_name)
            index_class = get_test_class_name(class_name, method_name, overload_index)
            retry_java  = retry_java.replace(base_class, index_class)

        java_code = retry_java
        test_path = save_test_file(
            config.GENERATED_TESTS_DIR, full_name, class_name, method_name,
            retry_java, overload_index,
        )
        compiled, passed, error = compile_and_run(
            test_path, full_name, class_name, method_name, overload_index,
        )
        retry_succeeded = compiled and passed

    # ------------------------------------------------------------------ #
    # SAVE FINAL RESULT                                                    #
    # ------------------------------------------------------------------ #
    if not compiled:
        status = 'COMPILE_FAILED'
    elif not passed:
        status = 'FAILED'
    else:
        status = 'PASSED'

    save_result(config.RESULTS_JSON, unique_key, {
        'status':          status,
        'path':            path,
        'retry_triggered': retry_triggered,
        'retry_succeeded': retry_succeeded,
        'retry_count':     retry_count,
        'error_message':   error,
        'test_file':       test_path,
    })
    return status
