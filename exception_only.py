#!/usr/bin/env python
"""
exception_only.py - Measure the "just assert an exception was thrown" smell
among PASSED tests, for a V1 (or V7) variant.

Motivation
----------
A test that does nothing but assert the method throws - no assertion on the
return value, on object state, or even on the exception's message - is a very
weak test. It passes (the exception is thrown), so V1 counts it as PASSED, but
it verifies almost no behaviour. The professor flagged this for V7
("12-21% of V7 PASSED tests just assert an exception was thrown"); this script
computes the same metric for any variant so V1 can be reported on the same
footing.

Definition (per @Test method)
------------------------------
A @Test method is "exception-only" iff:
  * it contains at least one exception-was-thrown check, i.e. an
    assertThrows / assertThrowsExactly / expectThrows call, OR a manual
    try { ...; fail(...) } catch (...) pattern, OR a JUnit4 @Test(expected=...);
  AND
  * it contains zero VALUE assertions - no assertEquals/assertTrue/assertNull/
    assertNotNull/assertSame/... and no assertion on the thrown exception
    (e.g. assertEquals(msg, ex.getMessage())). If the test additionally checks
    the message or a return value, it is doing more than "just" asserting a
    throw, so it is NOT counted.

assertDoesNotThrow is deliberately NOT treated as an exception-thrown check -
that is a separate "smoke test" smell, not "an exception was thrown".

A PASSED test FILE (one generated file == one method under test == one PASSED
entry in results.json) is counted as exception-only iff it has >= 1 @Test
method and EVERY @Test method in it is exception-only - i.e. the whole test for
that method does nothing but check that it throws.

Two denominators are reported:
  * per-file : exception-only files / PASSED files   (the headline number)
  * per-test : exception-only @Test methods / all @Test methods in PASSED files

Usage
-----
  python exception_only.py
      Uses ./config.py next to this script (the local gpt-5-mini V1 variant).

  python exception_only.py --config-path <path-to-config.py>
      Point at another variant's config.py (Gemini, DeepSeek, or a V7 config).

Output
------
Prints a summary to stdout and writes <results_dir>/exception_only.json with
the full per-file classification. Reads only; never mutates results.json.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime


# ---- @Test body extraction (same brace-counting approach as analyze_quality.py) ----
TEST_METHOD_RE = re.compile(
    r'@Test\b[\s\S]*?(?:void|public\s+void|protected\s+void)\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{',
    re.M,
)

# Exception-was-thrown checks.
THROW_ASSERT_RE = re.compile(r'\b(assertThrows|assertThrowsExactly|expectThrows)\s*\(')
# JUnit4 style annotation on the method (rare in these JUnit5 files, handled for safety).
EXPECTED_ANNO_RE = re.compile(r'@Test\s*\(\s*expected\s*=')

# VALUE assertions: any assertion that verifies a value/state, NOT the bare throw.
# assertThrows family and bare fail() are intentionally excluded here.
VALUE_ASSERT_RE = re.compile(
    r'\b(assertEquals|assertNotEquals|assertTrue|assertFalse|assertNull|assertNotNull|'
    r'assertSame|assertNotSame|assertArrayEquals|assertIterableEquals|assertLinesMatch|'
    r'assertInstanceOf|assertDoesNotThrow|assertTimeout|assertTimeoutPreemptively|'
    r'assertThat|assertAll)\s*\('
)

# Manual throw pattern: try { ... fail(...) ... } catch (...) { ... }
TRY_RE = re.compile(r'\btry\s*\{')
CATCH_RE = re.compile(r'\bcatch\s*\(')
FAIL_RE = re.compile(r'\bfail\s*\(')


def split_test_bodies(src):
    """Return list of (name, body, header) for each @Test method via brace counting.

    header is the text from the '@Test' token to the opening brace (so we can see
    a JUnit4 @Test(expected=...) annotation that belongs to this method).
    """
    out = []
    for m in TEST_METHOD_RE.finditer(src):
        start = m.end() - 1  # index of the opening '{'
        depth = 0
        i = start
        while i < len(src):
            c = src[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), src[start + 1:i], src[m.start():start]))
                    break
            i += 1
    return out


def is_exception_only_method(body, header):
    """True iff this @Test body's only verification is that an exception was thrown."""
    has_throw_assert = bool(THROW_ASSERT_RE.search(body))
    has_expected_anno = bool(EXPECTED_ANNO_RE.search(header))
    has_try_fail_catch = bool(
        TRY_RE.search(body) and CATCH_RE.search(body) and FAIL_RE.search(body)
    )
    throws_checked = has_throw_assert or has_expected_anno or has_try_fail_catch
    if not throws_checked:
        return False
    # Any value assertion at all disqualifies it (message check, return check, etc.).
    if VALUE_ASSERT_RE.search(body):
        return False
    return True


def classify_file(src):
    """Return (n_tests, n_exception_only_tests, file_is_exception_only)."""
    tests = split_test_bodies(src)
    n = len(tests)
    if n == 0:
        return 0, 0, False
    n_exc = sum(1 for _, body, header in tests if is_exception_only_method(body, header))
    file_is_exc = (n_exc == n)
    return n, n_exc, file_is_exc


def load_config(config_path):
    config_path = os.path.abspath(config_path)
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    spec = importlib.util.spec_from_file_location("variant_config", config_path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(config_path))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    for attr in ("RESULTS_JSON",):
        if not hasattr(mod, attr):
            raise AttributeError(f"{config_path} missing required attribute: {attr}")
    return mod


def analyze(config):
    with open(config.RESULTS_JSON, "r", encoding="utf-8") as f:
        results = json.load(f)
    # Support both flat results.json and the {_retro_summary, results} shape.
    if "results" in results and "_retro_summary" in results:
        results = results["results"]

    passed_files = 0
    passed_with_tests = 0
    exc_only_files = 0
    total_tests = 0
    exc_only_tests = 0
    missing = 0
    no_test_methods = 0
    examples = []

    for key, entry in results.items():
        if entry.get("status") != "PASSED":
            continue
        passed_files += 1
        tf = entry.get("test_file")
        if not tf or not os.path.isfile(tf):
            missing += 1
            continue
        try:
            with open(tf, "r", encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
        except OSError:
            missing += 1
            continue
        n, n_exc, file_is_exc = classify_file(src)
        if n == 0:
            no_test_methods += 1
            continue
        passed_with_tests += 1
        total_tests += n
        exc_only_tests += n_exc
        if file_is_exc:
            exc_only_files += 1
            if len(examples) < 25:
                examples.append({"key": key, "n_tests": n, "test_file": tf})

    summary = {
        "results_json": config.RESULTS_JSON,
        "passed_files": passed_files,
        "passed_files_with_test_methods": passed_with_tests,
        "passed_files_missing_or_unreadable": missing,
        "passed_files_with_no_test_methods": no_test_methods,
        "exception_only_files": exc_only_files,
        "exception_only_files_pct_of_passed": (
            100.0 * exc_only_files / passed_files if passed_files else 0.0
        ),
        "exception_only_files_pct_of_passed_with_tests": (
            100.0 * exc_only_files / passed_with_tests if passed_with_tests else 0.0
        ),
        "total_test_methods_in_passed": total_tests,
        "exception_only_test_methods": exc_only_tests,
        "exception_only_test_methods_pct": (
            100.0 * exc_only_tests / total_tests if total_tests else 0.0
        ),
        "examples": examples,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return summary


def print_report(summary):
    print()
    print("=" * 66)
    print('  "just assert an exception was thrown" among PASSED tests')
    print("=" * 66)
    print(f'  Results file                         : {summary["results_json"]}')
    print("-" * 66)
    print(f'  PASSED files                         : {summary["passed_files"]:>6}')
    print(f'    of which have @Test methods        : {summary["passed_files_with_test_methods"]:>6}')
    print(f'    missing/unreadable on disk         : {summary["passed_files_missing_or_unreadable"]:>6}')
    print(f'    no @Test methods found             : {summary["passed_files_with_no_test_methods"]:>6}')
    print("-" * 66)
    print(f'  Exception-only PASSED files          : {summary["exception_only_files"]:>6}')
    print(f'    % of all PASSED files              : {summary["exception_only_files_pct_of_passed"]:>5.1f}%')
    print(f'    % of PASSED files with @Tests      : {summary["exception_only_files_pct_of_passed_with_tests"]:>5.1f}%')
    print("-" * 66)
    print(f'  @Test methods in PASSED files        : {summary["total_test_methods_in_passed"]:>6}')
    print(f'  Exception-only @Test methods         : {summary["exception_only_test_methods"]:>6}')
    print(f'    % of @Test methods                 : {summary["exception_only_test_methods_pct"]:>5.1f}%')
    print("=" * 66)
    if summary["examples"]:
        print("\n  Sample exception-only PASSED tests (first 25):")
        for e in summary["examples"]:
            print(f'    [{e["n_tests"]} @Test] {e["key"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--config-path",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py"),
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config(args.config_path)
    summary = analyze(config)

    out = args.output or os.path.join(
        os.path.dirname(config.RESULTS_JSON), "exception_only.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print_report(summary)
    print(f"\nWrote -> {out}")


if __name__ == "__main__":
    main()
