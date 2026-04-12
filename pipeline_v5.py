"""
pipeline_v5.py — Agentic test generation pipeline.

Routes each method through the Complexity Classifier, then either:

  simple  → run_simple_path()  (plan → generate → allowlist → Maven)

  complex → Checklist agent
              └─ validate (≤2×, internal)
            Resource generator agent
              └─ validate (≤2×, internal)
              └─ if failed: strip resource_spec, fallback to run_simple_path(checklist=...)
            run_simple_path(checklist=..., resources=...)  ← full complex path
              └─ plan (with checklist + resources context)
              └─ generate
              └─ allowlist check
              └─ assemble (inject resources into test)
              └─ Maven
"""
from collections import Counter
from datetime import datetime

import config
from src.loader import load_methods
from src.result_tracker import load_results, save_result, is_already_processed
from src.reporter import print_progress, print_final_report
from src.context_loader import load_context_data, get_dependency_chain, get_caller_snippets
from src.resource_scanner import scan_test_resources, is_file_dependent
from src.complexity_classifier import classify, compute_clc
from src.checklist_agent import generate_checklist, validate_checklist
from src.resource_generator_agent import generate_resources, validate_resources
from src.simple_path import run_simple_path


def assign_unique_keys(methods):
    """Assign unique_key and overload_index to each method (same as pipeline_step3)."""
    counts = Counter(m['full_name'] for m in methods)
    seen   = Counter()
    for m in methods:
        fn = m['full_name']
        if counts[fn] > 1:
            m['unique_key']     = f"{fn}_overload_{seen[fn]}"
            m['overload_index'] = seen[fn]
        else:
            m['unique_key']     = fn
            m['overload_index'] = None
        seen[fn] += 1
    return methods


def _run_complex_path(method, dep_chains, call_graph, resource_files):
    """
    Orchestrates the complex path for one method:
      1. Checklist agent (generate + validate)
      2. Resource generator agent (generate + validate)
         → on failure: fallback to simple path with stripped checklist
      3. Full generation via run_simple_path with checklist + resources
    """
    unique_key      = method['unique_key']
    dep_chain       = get_dependency_chain(dep_chains, method)
    caller_snippets = get_caller_snippets(call_graph, method, max_snippets=2)
    clc             = compute_clc(method.get('body', ''))

    # ---- 1. CHECKLIST AGENT ------------------------------------------ #
    print("  [complex] Generating checklist...")
    checklist = generate_checklist(method, dep_chain, resource_files, caller_snippets)

    if checklist is None:
        print("  [complex] Checklist generation failed — saving CHECKLIST_FAILED.")
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          'CHECKLIST_FAILED',
            'path':            'complex',
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   'LLM did not return a parseable checklist.',
        })
        return

    ok, issues, checklist = validate_checklist(checklist, method, clc)
    if not ok:
        print(f"  [complex] Checklist validation exhausted — saving CHECKLIST_FAILED. "
              f"Issues: {issues}")
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          'CHECKLIST_FAILED',
            'path':            'complex',
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   f"Checklist invalid after retries: {'; '.join(issues)}",
        })
        return

    # ---- 2. RESOURCE GENERATOR AGENT --------------------------------- #
    print("  [complex] Generating resources...")
    resources = generate_resources(method, checklist, dep_chain, resource_files)

    if resources is None:
        # Parse / API failure — treat same as validation failure
        print("  [complex] Resource generation failed — falling back to simple path "
              "with checklist context.")
        stripped = {k: v for k, v in checklist.items() if k != 'resource_spec'}
        run_simple_path(
            method, dep_chains, call_graph,
            checklist=stripped,
            resource_fallback=True,
        )
        return

    ok, issues, resources = validate_resources(resources, method)
    if not ok:
        print(f"  [complex] Resource validation exhausted — falling back to simple path "
              f"with checklist context. Issues: {issues}")
        stripped = {k: v for k, v in checklist.items() if k != 'resource_spec'}
        run_simple_path(
            method, dep_chains, call_graph,
            checklist=stripped,
            resource_fallback=True,
        )
        return

    # ---- 3. FULL COMPLEX GENERATION ---------------------------------- #
    print("  [complex] Running full generation with checklist + resources...")
    run_simple_path(
        method, dep_chains, call_graph,
        checklist=checklist,
        resources=resources,
        resource_fallback=False,
    )


def run_pipeline():
    start_time = datetime.now()
    print("=" * 40)
    print("PIPELINE V5: AGENTIC TEST GENERATION")
    print("=" * 40)

    dep_chains, call_graph = load_context_data(
        config.DEPENDENCY_CHAINS_FILE, config.CALL_GRAPH_FILE
    )

    # Scan test resources once — passed into both paths
    resource_files_dict = scan_test_resources(config.TEST_RESOURCES_DIR)
    # Flatten to a list of relative paths for use in agent prompts
    resource_files_flat: list = [
        path
        for paths in resource_files_dict.values()
        for path in sorted(paths)
    ]

    methods   = assign_unique_keys(load_methods(config.INPUT_JSON))
    existing  = load_results(config.RESULTS_JSON)
    remaining = [
        m for m in methods
        if not is_already_processed(config.RESULTS_JSON, m['unique_key'])
    ]
    print(f"Already done: {len(existing)}")
    print(f"Remaining:    {len(remaining)}")

    for i, method in enumerate(remaining):
        method_name = method['method_name']
        path        = classify(method, dep_chains)

        print(f"\n[{i+1}/{len(remaining)}] {method_name}  [{path}]")

        # Only pass resource files when the method actually needs them
        resource_files = resource_files_flat if is_file_dependent(method) else []

        if path == 'simple':
            run_simple_path(method, dep_chains, call_graph)
        else:
            _run_complex_path(method, dep_chains, call_graph, resource_files)

        if (i + 1) % 10 == 0:
            all_results = load_results(config.RESULTS_JSON)
            print_progress(i + 1, len(remaining), all_results)

    all_results = load_results(config.RESULTS_JSON)
    print_final_report(all_results, config.FINAL_REPORT, start_time)


if __name__ == '__main__':
    run_pipeline()
