"""Detect the WORST smell: the test file redeclares the production class it claims
to test. This is a false-pass factory — the test compiles and runs against the
LLM's stub copy and never exercises production code."""
import os, re, json
import config

ROOT = config.GENERATED_TESTS_DIR
results = json.load(open(config.RESULTS_JSON, encoding='utf-8'))

# Map file -> status
file_to_status = {}
for k,v in results.items():
    tf = v.get('test_file')
    if tf:
        file_to_status[os.path.normpath(tf)] = v.get('status')

LOCAL_DECL_RE = re.compile(
    r'^\s*(?:public|private|protected|static|abstract|final|\s)*\s*(?:class|enum|interface)\s+(\w+)\b',
    re.M
)

shadow_files = []         # redeclares the EXACT production class
shadow_passed = []
helper_subclass_files = 0 # declares a subclass with a different name (legit)

for root,_,files in os.walk(ROOT):
    for f in files:
        if not f.endswith('.java'): continue
        path = os.path.normpath(os.path.join(root, f))
        src = open(path, encoding='utf-8', errors='ignore').read()
        # target class encoded in filename: <ClassName>_<method>(_idx)?_Test.java
        base = f[:-len('.java')]
        parts = base.split('_')
        # the test class itself is base; production class is parts[0]
        production_class = parts[0]
        test_class = base

        decls = LOCAL_DECL_RE.findall(src)
        # exclude the test class declaration itself
        decls = [d for d in decls if d != test_class]

        if production_class in decls:
            shadow_files.append((path, decls))
            if file_to_status.get(path) == 'PASSED':
                shadow_passed.append(path)
        elif decls:
            helper_subclass_files += 1

total_files = 1309
print(f"\n*** Production-class SHADOWING (test redeclares the class under test) ***")
print(f"  Files affected               : {len(shadow_files)} of {total_files} ({len(shadow_files)/total_files*100:.1f}%)")
print(f"  Of those, marked PASSED      : {len(shadow_passed)} (these are FALSE PASSES)")
print(f"\nFiles with other local types (likely legit test helpers): {helper_subclass_files}")

print("\nSample of shadowing files (first 25):")
for path, decls in shadow_files[:25]:
    rel = os.path.relpath(path, ROOT)
    status = file_to_status.get(path, '?')
    print(f"  [{status:<14}] {rel}")
    print(f"                  redeclared: {', '.join(decls[:8])}{'...' if len(decls)>8 else ''}")
