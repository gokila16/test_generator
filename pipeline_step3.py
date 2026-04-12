import os
from collections import Counter
from datetime import datetime
import config
from src.loader import load_methods
from src.result_tracker import load_results, is_already_processed
from src.reporter import print_progress, print_final_report
from src.context_loader import load_context_data
from src.simple_path import run_simple_path


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

    dep_chains, call_graph = load_context_data(
        config.DEPENDENCY_CHAINS_FILE, config.CALL_GRAPH_FILE
    )

    methods  = assign_unique_keys(load_methods(config.INPUT_JSON))
    existing = load_results(config.RESULTS_JSON)
    remaining = [
        m for m in methods
        if not is_already_processed(config.RESULTS_JSON, m['unique_key'])
    ]
    print(f"Already done: {len(existing)}")
    print(f"Remaining:    {len(remaining)}")

    for i, method in enumerate(remaining):
        print(f"\n[{i+1}/{len(remaining)}] {method['method_name']}")
        run_simple_path(method, dep_chains, call_graph)

        if (i + 1) % 10 == 0:
            all_results = load_results(config.RESULTS_JSON)
            print_progress(i + 1, len(remaining), all_results)

    all_results = load_results(config.RESULTS_JSON)
    print_final_report(all_results, config.FINAL_REPORT, start_time)


if __name__ == '__main__':
    run_pipeline()
