"""
run_generated_tests.py
----------------------
Re-runs all already-generated test files through Maven and updates results.json.
Does NOT call the LLM.

Usage:
    python run_generated_tests.py              # run every method that has a test file
    python run_generated_tests.py --only-failed  # re-run only COMPILE_FAILED / FAILED
"""

import argparse
import json
import os
from collections import Counter
from datetime import datetime

import config
from src.maven_runner import compile_and_run
from src.result_tracker import load_results, save_result
from src.reporter import print_final_report


def _find_test_methods(results, only_failed=False):
    """
    Returns a list of (unique_key, entry) pairs that have a test_file on disk.
    If only_failed=True, restrict to COMPILE_FAILED and FAILED.
    """
    wanted_statuses = {'COMPILE_FAILED', 'FAILED'} if only_failed else None
    found = []
    for key, entry in results.items():
        test_file = entry.get('test_file')
        if not test_file:
            continue
        if not os.path.exists(test_file):
            continue
        if wanted_statuses and entry.get('status') not in wanted_statuses:
            continue
        found.append((key, entry))
    return found


def _key_to_parts(unique_key):
    """
    Reconstructs (full_name, class_name, method_name, overload_index) from
    the unique_key stored in results.json.

    unique_key formats:
      org.apache.pdfbox.Foo.Bar.myMethod
      org.apache.pdfbox.Foo.Bar.myMethod_overload_0
    """
    overload_index = None
    key = unique_key

    # Check for overload suffix
    if '_overload_' in key:
        key, idx_str = key.rsplit('_overload_', 1)
        try:
            overload_index = int(idx_str)
        except ValueError:
            pass

    full_name = key
    parts = key.split('.')
    class_name = parts[-2] if len(parts) >= 2 else parts[-1]
    method_name = parts[-1]

    return full_name, class_name, method_name, overload_index


def run(only_failed=False):
    start_time = datetime.now()
    results = load_results(config.RESULTS_JSON)

    candidates = _find_test_methods(results, only_failed=only_failed)
    total = len(candidates)

    label = "COMPILE_FAILED + FAILED" if only_failed else "all methods with a test file"
    print("=" * 50)
    print("RUN GENERATED TESTS")
    print("=" * 50)
    print(f"Mode   : {label}")
    print(f"To run : {total}")
    print()

    if total == 0:
        print("Nothing to run.")
        return

    passed_count = 0
    failed_count = 0
    compile_fail_count = 0

    for i, (unique_key, entry) in enumerate(candidates, 1):
        test_file = entry['test_file']
        full_name, class_name, method_name, overload_index = _key_to_parts(unique_key)

        print(f"[{i}/{total}] {method_name}", end="  ", flush=True)

        compiled, passed, error = compile_and_run(
            test_file, full_name, class_name, method_name, overload_index
        )

        if not compiled:
            status = 'COMPILE_FAILED'
            compile_fail_count += 1
            print("COMPILE_FAILED")
        elif not passed:
            status = 'FAILED'
            failed_count += 1
            print("FAILED")
        else:
            status = 'PASSED'
            passed_count += 1
            print("PASSED")

        save_result(config.RESULTS_JSON, unique_key, {
            **entry,
            'status': status,
            'error_message': error,
            'rerun_at': datetime.now().isoformat(),
        })

        if i % 20 == 0:
            pct = i / total * 100
            print(f"\n  -- {i}/{total} ({pct:.0f}%) | "
                  f"PASSED={passed_count} FAILED={failed_count} "
                  f"COMPILE_FAILED={compile_fail_count} --\n")

    # Final stats
    print()
    all_results = load_results(config.RESULTS_JSON)
    print_final_report(all_results, config.FINAL_REPORT, start_time)

    elapsed = (datetime.now() - start_time).seconds
    print(f"\nRe-run summary for {total} tests:")
    print(f"  PASSED        : {passed_count}")
    print(f"  FAILED        : {failed_count}")
    print(f"  COMPILE_FAILED: {compile_fail_count}")
    print(f"  Time elapsed  : {elapsed}s")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Re-run generated tests without LLM')
    parser.add_argument(
        '--only-failed',
        action='store_true',
        help='Only re-run methods currently marked COMPILE_FAILED or FAILED',
    )
    args = parser.parse_args()
    run(only_failed=args.only_failed)
