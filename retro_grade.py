#!/usr/bin/env python
"""
retro_grade.py - Re-grade V1's already-generated Java tests using V7's
extraction-stage quality gates, so V1 and V7 can be compared on the same rubric.

Why this script exists
----------------------
V7's pipeline rejects an LLM-generated test BEFORE compilation if any of these
conditions hold (V7 calls this 'extraction failure'):

  * missing @Test annotation                   -> no_test_methods
  * trivial assertions (assertTrue(true) etc.) -> trivial_assertions
  * class-under-test never referenced in body  -> sut_missing
  * top-level class redefinition / shadowing   -> class_redefinition
  * nested-class shadowing of production class -> nested_class_redefinition
  * mockito import                             -> mockito_import
  * file truncated                             -> truncated
  * constructor-arity mismatch                 -> constructor_arity:*

V1's pipeline does NOT enforce any of these post-hoc - any test that compiles
and runs is counted as PASSED. That inflates V1's apparent pass rate relative
to V7. This script applies V7's exact extraction validator (post_process_java)
to every V1 PASSED test on disk and reclassifies any rejected entry into a new
'extraction_failed_retro' bucket, producing a defensible apples-to-apples
comparison.

This is a pure static-analysis pass: no LLM calls, no Maven invocations.

Usage
-----
  python retro_grade.py
      Reads ./test_generator/config.py relative to cwd (= the local V1 variant).

  python retro_grade.py --config-path <path-to-config.py>
      Re-grade a different V1 variant (Gemini, DeepSeek) by pointing at its
      config.py. The config must expose RESULTS_JSON and GENERATED_TESTS_DIR.

Output
------
- Prints a before/after summary to stdout.
- Writes <RESULTS_DIR>/results_retro.json with the re-graded statuses plus a
  '_retro_summary' stanza. The original results.json is left untouched.
"""

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime


# V7's extraction validator lives here. The script will import post_process_java
# from this directory at runtime so we use V7's grading code verbatim.
V7_SRC_DIR = r'c:\Users\Harini\Documents\thesis_research\test_generator_v7\test_generator\src'


def load_v1_config(config_path):
    """Dynamically import a V1 variant's config.py as a module."""
    config_path = os.path.abspath(config_path)
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    # Some configs (Gemini, DeepSeek) call dotenv.load_dotenv() at import; that
    # is harmless. We just need the path constants.
    spec = importlib.util.spec_from_file_location("v1_config", config_path)
    mod = importlib.util.module_from_spec(spec)
    # Ensure the config's own directory is importable in case it has sibling
    # imports (none of the V1 configs do, but be defensive).
    sys.path.insert(0, os.path.dirname(config_path))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    for attr in ("RESULTS_JSON", "GENERATED_TESTS_DIR"):
        if not hasattr(mod, attr):
            raise AttributeError(f"{config_path} is missing required attribute: {attr}")
    return mod


def load_v7_validator():
    """Import V7's post_process_java without disturbing the rest of sys.path."""
    if not os.path.isdir(V7_SRC_DIR):
        raise FileNotFoundError(
            f"V7 source directory not found: {V7_SRC_DIR}. "
            "Edit V7_SRC_DIR at the top of this script if V7 has moved."
        )
    sys.path.insert(0, V7_SRC_DIR)
    try:
        from java_post_processor import post_process_java  # type: ignore
    finally:
        # Keep V7_SRC_DIR on the path - the imported module may lazily reference
        # sibling helpers (none currently do, but be safe).
        pass
    return post_process_java


def derive_grading_params(result_key, test_file_path, generated_tests_dir):
    """
    Derive (sut_class_name, expected_package, test_class_name) for one result
    entry. Mirrors the values V7's pipeline passes to post_process_java.

    result_key example: 'org.apache.pdfbox.Loader.loadFDF_overload_0'
    test_file_path   : '<...>\generated_testsgpt5mini_v1\org\apache\pdfbox\Loader_loadFDF_0_Test.java'

    The package is read from the .java file's directory structure relative to
    GENERATED_TESTS_DIR (authoritative). The SUT class is taken from the result
    key's second-to-last dot-segment - this matches V7's convention and handles
    inner-class methods adequately: for an inner-class method like
    'pkg.Outer.Inner.method' the SUT becomes 'Inner' and the package is read
    from the file path (which omits 'Outer', as it should).
    """
    norm_test = os.path.normpath(test_file_path)
    norm_gen = os.path.normpath(generated_tests_dir)
    try:
        rel = os.path.relpath(norm_test, norm_gen)
    except ValueError:
        # Different drives or otherwise unrelatable - fall back to deriving the
        # package from the result key.
        rel = None

    if rel and not rel.startswith('..'):
        pkg_parts = rel.split(os.sep)[:-1]
        expected_package = '.'.join(pkg_parts) if pkg_parts else ''
    else:
        # Fallback: package = all but the last two segments of the key.
        key_parts = result_key.split('.')
        expected_package = '.'.join(key_parts[:-2]) if len(key_parts) >= 2 else ''

    test_class_name = os.path.splitext(os.path.basename(norm_test))[0]

    key_parts = result_key.split('.')
    sut_class_name = key_parts[-2] if len(key_parts) >= 2 else None

    return sut_class_name, expected_package, test_class_name


def regrade(config, post_process_java):
    """
    Walk results.json, re-grade every entry that was originally PASSED.

    Returns a tuple (regraded_results, summary_dict).
    - regraded_results: deep copy of original results with reclassified entries.
    - summary_dict: aggregate counts and reason breakdown.
    """
    results_path = config.RESULTS_JSON
    gen_dir = config.GENERATED_TESTS_DIR

    with open(results_path, 'r', encoding='utf-8') as f:
        original = json.load(f)

    regraded = {k: dict(v) for k, v in original.items()}

    original_counts = Counter(v.get('status', 'UNKNOWN') for v in original.values())
    retro_counts = Counter()
    reclassified = []
    reason_breakdown = Counter()
    missing_files = []
    read_errors = []

    for key, entry in original.items():
        status = entry.get('status')
        if status != 'PASSED':
            retro_counts[status or 'UNKNOWN'] += 1
            continue

        test_file = entry.get('test_file')
        if not test_file or not os.path.isfile(test_file):
            # Cannot re-grade if the .java file isn't on disk; preserve original
            # status but flag it.
            missing_files.append(key)
            retro_counts['PASSED'] += 1
            continue

        try:
            with open(test_file, 'r', encoding='utf-8') as f:
                java_code = f.read()
        except (OSError, UnicodeDecodeError) as e:
            read_errors.append((key, str(e)))
            retro_counts['PASSED'] += 1
            continue

        sut, pkg, test_class = derive_grading_params(key, test_file, gen_dir)

        # class_inventory=None: V7's constructor_arity and nested_class_redefinition
        # checks no-op without an inventory. The core four gates the professor
        # named (no_test_methods, trivial_assertions, sut_missing, plus
        # class_redefinition which catches anonymous-subclass-style structural
        # problems) all run regardless.
        try:
            _, reason = post_process_java(
                java_code,
                expected_package=pkg,
                test_class_name=test_class,
                sut_class_name=sut,
                class_inventory=None,
            )
        except Exception as e:
            read_errors.append((key, f"post_process_java raised: {e}"))
            retro_counts['PASSED'] += 1
            continue

        if reason is None:
            retro_counts['PASSED'] += 1
        else:
            # Reclassify to the retro bucket and stash the rejection reason.
            regraded[key]['status'] = 'EXTRACTION_FAILED_RETRO'
            regraded[key]['retro_grade_reason'] = reason
            regraded[key]['original_status'] = 'PASSED'
            retro_counts['EXTRACTION_FAILED_RETRO'] += 1
            reclassified.append((key, reason))
            reason_breakdown[reason] += 1

    summary = {
        'original_counts': dict(original_counts),
        'retro_counts': dict(retro_counts),
        'reclassified_count': len(reclassified),
        'reason_breakdown': dict(reason_breakdown),
        'reclassified_keys': [{'key': k, 'reason': r} for k, r in reclassified],
        'missing_test_files': missing_files,
        'read_errors': [{'key': k, 'error': e} for k, e in read_errors],
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'config_results_json': results_path,
        'config_generated_tests_dir': gen_dir,
        'v7_validator_path': os.path.join(V7_SRC_DIR, 'java_post_processor.py'),
    }
    return regraded, summary


def pass_rate(counts):
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return 100.0 * counts.get('PASSED', 0) / total


def print_report(summary):
    orig = summary['original_counts']
    retro = summary['retro_counts']
    all_cats = sorted(set(orig) | set(retro))

    print()
    print('=' * 64)
    print('V1 (original)  vs  V1 (V7-graded retro)')
    print('=' * 64)
    print(f'  Results file : {summary["config_results_json"]}')
    print(f'  Java files   : {summary["config_generated_tests_dir"]}')
    print(f'  V7 validator : {summary["v7_validator_path"]}')
    print('-' * 64)
    print(f'  {"Category":<32} {"Original":>10} {"Retro":>10}')
    print('-' * 64)
    for cat in all_cats:
        o = orig.get(cat, 0)
        r = retro.get(cat, 0)
        marker = '  <-- new' if cat == 'EXTRACTION_FAILED_RETRO' and o == 0 else ''
        print(f'  {cat:<32} {o:>10} {r:>10}{marker}')
    print('-' * 64)
    total = sum(orig.values())
    print(f'  Total methods                    {total:>10}')
    print(f'  Pass rate (original)             {pass_rate(orig):>9.1f}%')
    print(f'  Pass rate (V7-graded)            {pass_rate(retro):>9.1f}%')
    delta = pass_rate(retro) - pass_rate(orig)
    sign = '+' if delta >= 0 else ''
    print(f'  Delta                             {sign}{delta:>8.1f}pp')
    print('=' * 64)

    if summary['reason_breakdown']:
        print()
        print('Reclassification reasons:')
        for reason, n in sorted(
            summary['reason_breakdown'].items(), key=lambda kv: -kv[1]
        ):
            print(f'  {reason:<30} {n:>4}')

    if summary['missing_test_files']:
        print()
        print(f'NOTE: {len(summary["missing_test_files"])} PASSED entries had a '
              'missing test_file on disk and could not be re-graded. They were '
              'left as PASSED in the retro output.')

    if summary['read_errors']:
        print()
        print(f'NOTE: {len(summary["read_errors"])} entries hit read/validator '
              'errors. See results_retro.json for details.')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    parser.add_argument(
        '--config-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py'),
        help='Path to a V1 variant config.py (defaults to ./config.py next to this script).',
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Where to write the regraded JSON. Defaults to '
             '<results_dir>/results_retro.json next to the input results.json.',
    )
    args = parser.parse_args()

    config = load_v1_config(args.config_path)
    post_process_java = load_v7_validator()

    regraded, summary = regrade(config, post_process_java)

    output_path = args.output or os.path.join(
        os.path.dirname(config.RESULTS_JSON), 'results_retro.json'
    )
    output_payload = {'_retro_summary': summary, 'results': regraded}
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_payload, f, indent=2)

    print_report(summary)
    print()
    print(f'Wrote regraded results -> {output_path}')


if __name__ == '__main__':
    main()
