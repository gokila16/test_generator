"""Heuristic quality analysis of the generated JUnit tests.

Scans every .java file under GENERATED_TESTS_DIR and reports on common
test-quality smells: trivial assertions, missing assertions, control flow
inside tests (a proxy for re-implementing production logic), excessive
mocking, broad exception swallowing, etc.
"""
import os, re, json, statistics
import config
from collections import Counter, defaultdict

ROOT = config.GENERATED_TESTS_DIR
results = json.load(open(config.RESULTS_JSON, encoding='utf-8'))

ASSERT_CALLS = [
    'assertEquals', 'assertNotEquals', 'assertTrue', 'assertFalse',
    'assertNull', 'assertNotNull', 'assertSame', 'assertNotSame',
    'assertArrayEquals', 'assertIterableEquals', 'assertLinesMatch',
    'assertThrows', 'assertDoesNotThrow', 'assertAll', 'assertTimeout',
    'fail',
]
ASSERT_RE = re.compile(r'\b(' + '|'.join(ASSERT_CALLS) + r')\s*\(')

TEST_METHOD_RE = re.compile(r'@Test[\s\S]*?(?:void|public\s+void)\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{', re.M)


def split_test_bodies(src):
    """Return list of (name, body) for each @Test method using brace counting."""
    out = []
    for m in TEST_METHOD_RE.finditer(src):
        start = m.end() - 1   # position of opening '{'
        depth = 0
        i = start
        while i < len(src):
            c = src[i]
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), src[start+1:i]))
                    break
            i += 1
    return out


def classify_file(path, src):
    """Return dict of flags + counts for one test file."""
    fname = os.path.basename(path)
    # method name encoded in filename: ClassName_methodName(_index)?_Test.java
    base = fname[:-len('.java')]
    parts = base.split('_')
    # naive: target method = second-to-last (before _Test) or before optional index
    target_method = None
    if len(parts) >= 3 and parts[-1] == 'Test':
        if parts[-2].isdigit():
            target_method = parts[-3] if len(parts) >= 4 else None
        else:
            target_method = parts[-2]

    tests = split_test_bodies(src)
    num_tests = len(tests)
    total_asserts = 0
    tests_with_no_assert = 0
    tests_with_only_assertNotNull = 0
    tests_with_trivial_only = 0      # only assertTrue(true)/false-false/etc
    tests_with_control_flow = 0      # if/for/while/switch in body
    tests_with_empty_catch = 0
    test_lines = []

    for tname, body in tests:
        kinds = ASSERT_RE.findall(body)
        n_assert = len(kinds)
        total_asserts += n_assert
        if n_assert == 0:
            tests_with_no_assert += 1
        unique_kinds = set(kinds)
        if unique_kinds == {'assertNotNull'}:
            tests_with_only_assertNotNull += 1
        # Trivial patterns
        trivial = (
            re.search(r'assertTrue\s*\(\s*true\s*\)', body) or
            re.search(r'assertFalse\s*\(\s*false\s*\)', body) or
            re.search(r'assertNull\s*\(\s*null\s*\)', body) or
            re.search(r'assertEquals\s*\(\s*(\d+|"[^"]*"|true|false|null)\s*,\s*\1\s*\)', body)
        )
        # "only trivial" = at least one trivial AND no non-trivial assertions other than trivial/notNull
        if trivial and unique_kinds.issubset({'assertTrue', 'assertFalse', 'assertNull', 'assertNotNull', 'assertEquals'}):
            # weak signal; flag if NO assertEquals with computed exp OR everything is trivial
            if n_assert <= sum(1 for k in kinds if k in {'assertTrue', 'assertFalse', 'assertNull', 'assertNotNull'}):
                tests_with_trivial_only += 1
        if re.search(r'\bif\s*\(|\bfor\s*\(|\bwhile\s*\(|\bswitch\s*\(', body):
            tests_with_control_flow += 1
        if re.search(r'catch\s*\([^)]+\)\s*\{\s*\}', body):
            tests_with_empty_catch += 1
        test_lines.append(body.count('\n'))

    uses_mockito = bool(re.search(r'\borg\.mockito\b|\bMockito\b|\bmock\s*\(|\bwhen\s*\(|\bverify\s*\(', src))
    uses_disabled = '@Disabled' in src
    uses_paramtest = '@ParameterizedTest' in src
    has_assertThrows = 'assertThrows' in src
    has_doesNotThrow = 'assertDoesNotThrow' in src

    # Does the file actually call the target method?
    calls_target = False
    if target_method:
        calls_target = bool(re.search(r'\b' + re.escape(target_method) + r'\s*\(', src))

    return {
        'file': os.path.relpath(path, ROOT),
        'num_tests': num_tests,
        'total_asserts': total_asserts,
        'tests_with_no_assert': tests_with_no_assert,
        'tests_with_only_assertNotNull': tests_with_only_assertNotNull,
        'tests_with_trivial_only': tests_with_trivial_only,
        'tests_with_control_flow': tests_with_control_flow,
        'tests_with_empty_catch': tests_with_empty_catch,
        'uses_mockito': uses_mockito,
        'uses_disabled': uses_disabled,
        'uses_paramtest': uses_paramtest,
        'has_assertThrows': has_assertThrows,
        'has_doesNotThrow': has_doesNotThrow,
        'calls_target_method': calls_target,
        'target_method': target_method,
        'mean_test_lines': statistics.mean(test_lines) if test_lines else 0,
    }


# ---------- collect ----------
records = []
for root,_,files in os.walk(ROOT):
    for f in files:
        if not f.endswith('.java'):
            continue
        p = os.path.join(root, f)
        try:
            src = open(p, encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        records.append(classify_file(p, src))

# attach result status
key_to_status = {}
for k, v in results.items():
    # results key is full_name; file path encodes class_method[_index]_Test
    pass  # too noisy to map cleanly; we'll do status separately

# ---------- aggregate ----------
N = len(records)
print(f"Files scanned: {N}\n")

def pct(n): return f"{n} ({n/N*100:.1f}%)"

total_tests = sum(r['num_tests'] for r in records)
total_asserts = sum(r['total_asserts'] for r in records)

print("=== Per-file aggregates ===")
print(f"  Files with 0 @Test methods                : {pct(sum(1 for r in records if r['num_tests']==0))}")
print(f"  Files where target method NOT called      : {pct(sum(1 for r in records if not r['calls_target_method']))}")
print(f"  Files using Mockito                       : {pct(sum(1 for r in records if r['uses_mockito']))}")
print(f"  Files using @Disabled                     : {pct(sum(1 for r in records if r['uses_disabled']))}")
print(f"  Files using @ParameterizedTest            : {pct(sum(1 for r in records if r['uses_paramtest']))}")
print(f"  Files with any assertThrows               : {pct(sum(1 for r in records if r['has_assertThrows']))}")
print(f"  Files with any assertDoesNotThrow         : {pct(sum(1 for r in records if r['has_doesNotThrow']))}")
print()
print("=== Per-test aggregates ===")
print(f"  Total @Test methods                       : {total_tests}")
print(f"  Avg @Tests per file                       : {total_tests/N:.2f}")
print(f"  Total assertion calls                     : {total_asserts}")
print(f"  Avg assertions per @Test                  : {total_asserts/total_tests:.2f}" if total_tests else "")
print()
n_no_assert = sum(r['tests_with_no_assert'] for r in records)
n_only_notnull = sum(r['tests_with_only_assertNotNull'] for r in records)
n_trivial_only = sum(r['tests_with_trivial_only'] for r in records)
n_control = sum(r['tests_with_control_flow'] for r in records)
n_empty_catch = sum(r['tests_with_empty_catch'] for r in records)
print(f"  @Tests with ZERO assertions               : {n_no_assert} ({n_no_assert/total_tests*100:.1f}%)")
print(f"  @Tests whose only assertion is assertNotNull: {n_only_notnull} ({n_only_notnull/total_tests*100:.1f}%)")
print(f"  @Tests with ONLY trivial/notNull asserts   : {n_trivial_only} ({n_trivial_only/total_tests*100:.1f}%)")
print(f"  @Tests with control flow (if/for/while/sw) : {n_control} ({n_control/total_tests*100:.1f}%)  <- proxy for re-implementing logic")
print(f"  @Tests with empty catch block              : {n_empty_catch} ({n_empty_catch/total_tests*100:.1f}%)")

# Dump worst offenders for follow-up reading
def first_files(predicate, k=8):
    return [r['file'] for r in records if predicate(r)][:k]

print("\n=== Example files for spot-checking ===")
print(" -- files where target method is NOT called --")
for f in first_files(lambda r: not r['calls_target_method']): print("   ", f)
print(" -- files with @Tests containing control flow --")
for f in first_files(lambda r: r['tests_with_control_flow']>0): print("   ", f)
print(" -- files where only assertion is assertNotNull --")
for f in first_files(lambda r: r['tests_with_only_assertNotNull']>0): print("   ", f)
print(" -- files with @Tests having ZERO assertions --")
for f in first_files(lambda r: r['tests_with_no_assert']>0): print("   ", f)
print(" -- files using Mockito --")
for f in first_files(lambda r: r['uses_mockito']): print("   ", f)
print(" -- files using @Disabled --")
for f in first_files(lambda r: r['uses_disabled']): print("   ", f)
