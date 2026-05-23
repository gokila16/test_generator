"""
recover_allowlist_failures.py

One-off recovery: re-validates saved LLM responses for ALLOWLIST_FAILED
methods using the current allowlist checker. For responses that now pass,
saves the test file, runs Maven, and updates results.json with the real
outcome. Skips re-prompting the LLM, so this is fast and free.

Run AFTER updating src/allowlist_checker.py with the SUT-self / inventory
fallback fixes. Works against the testable-run artifacts via config_testable.

Usage:
    python recover_allowlist_failures.py
    python recover_allowlist_failures.py --dry-run     # report-only, no Maven, no writes
"""

import argparse
import json
import os
from collections import Counter

# Trigger config_testable's path overrides + maven_runner subprocess shim.
import config_testable  # noqa: F401

import config
from src.allowlist_checker import check_against_allowlist, validate_imports
from src.code_extractor import extract_java_code
from src.context_loader import load_class_inventory
from src.file_manager import save_test_file, get_test_class_name, sanitize_name
from src.java_post_processor import post_process_java, apply_java_fixes
from src.loader import load_methods
from src.maven_runner import compile_and_run
from src.result_tracker import load_results, save_result


def assign_unique_keys(methods):
    """Mirror of pipeline_step3.assign_unique_keys."""
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


def latest_response(unique_key, full_name):
    """Most recent saved response for this unique_key.
    Order: allowlist-retry > maven-retry > base.
    Some retry files were saved under full_name (not unique_key) for
    overloads, so check both forms.
    """
    candidates = []
    for ident in (unique_key, full_name):
        base = sanitize_name(ident)
        for suffix in ('_allowlist_response.txt',
                       '_retry_response.txt',
                       '_response.txt'):
            candidates.append(os.path.join(config.RESPONSES_DIR, base + suffix))
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _extract_and_process(response_text, class_inventory, expected_package,
                          test_class_name, sut_class_name):
    raw, extract_reason = extract_java_code(response_text)
    if raw is None:
        return None, None, extract_reason
    code, pp_reason = post_process_java(
        raw,
        class_inventory=class_inventory,
        expected_package=expected_package,
        test_class_name=test_class_name,
        sut_class_name=sut_class_name,
    )
    return code, raw, pp_reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would change without writing or running Maven.')
    args = parser.parse_args()

    print('=' * 58)
    print('RECOVER ALLOWLIST FAILURES (testable run)')
    print('=' * 58)
    print(f'  RESULTS_JSON         : {config.RESULTS_JSON}')
    print(f'  RESPONSES_DIR        : {config.RESPONSES_DIR}')
    print(f'  GENERATED_TESTS_DIR  : {config.GENERATED_TESTS_DIR}')
    print(f'  CLASS_INVENTORY_FILE : {config.CLASS_INVENTORY_FILE}')
    print(f'  INPUT_JSON           : {config.INPUT_JSON}')
    if args.dry_run:
        print('  >> DRY RUN: no writes, no Maven invocations')
    print('=' * 58)

    class_inventory = load_class_inventory(config.CLASS_INVENTORY_FILE)
    methods = assign_unique_keys(load_methods(config.INPUT_JSON))
    methods_by_key = {m['unique_key']: m for m in methods}

    results = load_results(config.RESULTS_JSON)
    targets = [(k, v) for k, v in results.items()
               if v.get('status') == 'ALLOWLIST_FAILED']
    print(f'\nALLOWLIST_FAILED entries to process: {len(targets)}\n')

    outcomes = Counter()
    new_violations = []

    for i, (unique_key, _result) in enumerate(targets, 1):
        method = methods_by_key.get(unique_key)
        if not method:
            print(f'[{i}/{len(targets)}] {unique_key}  -- not in metadata, skipping')
            outcomes['SKIPPED_NO_METHOD'] += 1
            continue

        full_name      = method['full_name']
        class_name     = method['class_name']
        method_name    = method['method_name']
        overload_index = method['overload_index']
        sut_pkg        = full_name[: -(len(method_name) + 1 + len(class_name) + 1)]
        # safer: derive package from full_name minus class_name.method_name
        sut_pkg = full_name.rsplit('.', 2)[0] if full_name.count('.') >= 2 else ''

        print(f'[{i}/{len(targets)}] {unique_key}')

        resp_path = latest_response(unique_key, full_name)
        if not resp_path:
            print(f'    no saved response  -- leaving as-is')
            outcomes['NO_RESPONSE'] += 1
            continue

        with open(resp_path, encoding='utf-8', errors='replace') as f:
            response_text = f.read()

        base_test_class = get_test_class_name(class_name, method_name)
        java_code, raw_code, fail_reason = _extract_and_process(
            response_text, class_inventory, sut_pkg,
            base_test_class, class_name,
        )
        if not java_code and raw_code is not None:
            # mirror Tier 3 behaviour: hand raw to Maven anyway
            java_code = apply_java_fixes(raw_code, expected_package=sut_pkg)
        if not java_code:
            print(f'    extraction failed ({fail_reason})  -- leaving as-is')
            outcomes['EXTRACT_FAILED'] += 1
            continue

        # Re-run allowlist with the fixed checker
        imp_v = validate_imports(java_code,
                                 method.get('source_file_imports', []),
                                 class_inventory)
        if imp_v:
            print(f'    still flagged (imports): {imp_v}')
            outcomes['STILL_FLAGGED'] += 1
            new_violations.append((unique_key, imp_v))
            if not args.dry_run:
                save_result(config.RESULTS_JSON, unique_key, {
                    'status':                'ALLOWLIST_FAILED',
                    'allowlist_violations':  imp_v,
                    'allowlist_retry_count': 0,
                    'retry_triggered':       False,
                    'retry_succeeded':       None,
                    'error_message':         f'Hallucinated methods: {", ".join(imp_v)}',
                })
            continue

        passed, viols = check_against_allowlist(java_code, method, class_inventory)
        if not passed:
            print(f'    still flagged: {viols}')
            outcomes['STILL_FLAGGED'] += 1
            new_violations.append((unique_key, viols))
            if not args.dry_run:
                save_result(config.RESULTS_JSON, unique_key, {
                    'status':                'ALLOWLIST_FAILED',
                    'allowlist_violations':  viols,
                    'allowlist_retry_count': 0,
                    'retry_triggered':       False,
                    'retry_succeeded':       None,
                    'error_message':         f'Hallucinated methods: {", ".join(viols)}',
                })
            continue

        # Allowlist passes — save test, run Maven, update results.json
        if overload_index is not None:
            base_class  = get_test_class_name(class_name, method_name)
            index_class = get_test_class_name(class_name, method_name, overload_index)
            java_code   = java_code.replace(base_class, index_class)

        if args.dry_run:
            print(f'    PASSES allowlist  -- (dry-run, skipping Maven)')
            outcomes['WOULD_RECOVER'] += 1
            continue

        test_path = save_test_file(
            config.GENERATED_TESTS_DIR, full_name, class_name, method_name,
            java_code, overload_index, class_inventory=class_inventory,
        )
        compiled, mvn_passed, error = compile_and_run(
            test_path, full_name, class_name, method_name, overload_index,
            class_inventory=class_inventory,
        )

        if compiled and mvn_passed:
            status = 'PASSED'
            outcomes['RECOVERED_PASSED'] += 1
        elif compiled:
            status = 'FAILED'
            outcomes['RECOVERED_TEST_FAILED'] += 1
        else:
            status = 'COMPILE_FAILED'
            outcomes['RECOVERED_COMPILE_FAILED'] += 1

        print(f'    -> {status}')
        save_result(config.RESULTS_JSON, unique_key, {
            'status':          status,
            'retry_triggered': False,
            'retry_succeeded': None,
            'error_message':   error if not (compiled and mvn_passed) else None,
        })

    print('\n' + '=' * 58)
    print('RECOVERY SUMMARY')
    print('=' * 58)
    for k, v in sorted(outcomes.items()):
        print(f'  {k:<26} {v}')
    print()

    if new_violations:
        print(f'Methods still failing allowlist ({len(new_violations)}):')
        for k, v in new_violations:
            print(f'  {k}')
            for vv in v:
                print(f'      {vv}')


if __name__ == '__main__':
    main()
