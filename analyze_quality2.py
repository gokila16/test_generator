"""Second-pass quality scan: deeper smells + per-status breakdown."""
import os, re, json
import config
from collections import Counter, defaultdict

ROOT = config.GENERATED_TESTS_DIR
results = json.load(open(config.RESULTS_JSON, encoding='utf-8'))

# Map filename -> status by walking results (results keyed by full_name, file by class_method[_idx]_Test.java)
# Build reverse: filename -> result entry, using stored test_file path.
file_to_status = {}
for k, v in results.items():
    tf = v.get('test_file')
    if tf:
        file_to_status[os.path.normpath(tf)] = v.get('status')

ASSERT_CALLS = ['assertEquals','assertNotEquals','assertTrue','assertFalse',
                'assertNull','assertNotNull','assertSame','assertNotSame',
                'assertArrayEquals','assertIterableEquals','assertLinesMatch',
                'assertThrows','assertDoesNotThrow','assertAll','assertTimeout','fail']
ASSERT_RE = re.compile(r'\b(' + '|'.join(ASSERT_CALLS) + r')\s*\(')
TEST_METHOD_RE = re.compile(r'@Test[\s\S]*?(?:void|public\s+void)\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{', re.M)

def split_test_bodies(src):
    out = []
    for m in TEST_METHOD_RE.finditer(src):
        start = m.end() - 1
        depth = 0
        i = start
        while i < len(src):
            c = src[i]
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), src[start+1:i])); break
            i += 1
    return out

per_status_kind_counter = defaultdict(Counter)   # status -> Counter(assertkind)
status_files = Counter()
flag_files = defaultdict(list)  # smell -> [files]

# global per-test counters
ndoes_only = 0
nthrows_only = 0
nnotnull_only = 0
nstate_assert = 0           # assertEquals / assertArrayEquals / assertSame = "strong" state check
total_tests = 0

REFLECTION_RE = re.compile(r'\bClass\.forName\b|\bgetDeclaredField\b|\bgetDeclaredMethod\b|\bsetAccessible\b')
LOCAL_DECL_RE = re.compile(r'^\s*(?:public|private|static|abstract|final|\s)*\s*(?:class|enum|interface)\s+(\w+)', re.M)

for root,_,files in os.walk(ROOT):
    for f in files:
        if not f.endswith('.java'): continue
        path = os.path.normpath(os.path.join(root, f))
        src = open(path, encoding='utf-8', errors='ignore').read()
        status = file_to_status.get(path, 'UNKNOWN')
        status_files[status] += 1

        # Smells: reflection / locally-declared types
        if REFLECTION_RE.search(src):
            flag_files['uses_reflection'].append(os.path.relpath(path, ROOT))
        # Local class/enum declarations (excluding the main Test class itself)
        main_class = os.path.basename(path)[:-len('.java')]
        local_types = [name for name in LOCAL_DECL_RE.findall(src) if name != main_class]
        if local_types:
            flag_files['locally_declared_types'].append(os.path.relpath(path, ROOT) + '  ->  ' + ','.join(local_types))

        tests = split_test_bodies(src)
        for tname, body in tests:
            total_tests += 1
            kinds = ASSERT_RE.findall(body)
            uniq = set(kinds)
            per_status_kind_counter[status].update(kinds)
            if uniq == {'assertDoesNotThrow'}:
                ndoes_only += 1
            if uniq == {'assertThrows'}:
                nthrows_only += 1
            if uniq == {'assertNotNull'}:
                nnotnull_only += 1
            if {'assertEquals','assertArrayEquals','assertSame','assertIterableEquals','assertLinesMatch'} & uniq:
                nstate_assert += 1

print(f"Total @Test methods across all files: {total_tests}\n")

print("=== Per-test weak-assertion patterns ===")
print(f"  Only assertion is assertDoesNotThrow : {ndoes_only}  ({ndoes_only/total_tests*100:.1f}%)  <- 'didn't crash'")
print(f"  Only assertion is assertThrows       : {nthrows_only} ({nthrows_only/total_tests*100:.1f}%)")
print(f"  Only assertion is assertNotNull      : {nnotnull_only} ({nnotnull_only/total_tests*100:.1f}%)")
print(f"  Has a STATE assert (eq/array/same)   : {nstate_assert} ({nstate_assert/total_tests*100:.1f}%)  <- strongest signal of real verification")
print()

print("=== Assertion-kind distribution by result status ===")
for st in ('PASSED','FAILED','COMPILE_FAILED'):
    c = per_status_kind_counter[st]
    total = sum(c.values())
    print(f"  -- {st} ({status_files[st]} files) -- total assertion calls: {total}")
    for k,n in c.most_common():
        print(f"      {k:<22} {n} ({n/total*100:.1f}%)")
    print()

print("=== Suspicious-pattern file lists (top 8 each) ===")
for tag, lst in flag_files.items():
    print(f"  [{tag}] {len(lst)} files")
    for f in lst[:8]:
        print(f"     {f}")
    print()
