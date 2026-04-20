import re
import config
from src.resource_scanner import is_file_dependent, scan_test_resources


def _format_imports(method):
    imports = method.get('source_file_imports', [])
    if not imports:
        return ""
    lines = ["// SOURCE FILE IMPORTS (use these when adding imports — do NOT add imports for unlisted classes):"]
    for imp in imports:
        lines.append(f"//   {imp}")
    return "\n".join(lines)



def _format_dependency_signatures(method):
    deps = method.get('dependency_signatures', [])
    if not deps:
        return ""
    by_class = {}
    for d in deps:
        by_class.setdefault(d['class_name'], []).append(d)
    lines = ["DEPENDENCY SIGNATURES (for these classes, ONLY the listed methods may be called — see AVAILABLE PROJECT CLASSES for additional callable classes):"]
    for cls, sigs in by_class.items():
        lines.append(f"  Class: {cls}")
        for d in sigs:
            lines.append(f"    [{d['kind']}] {d['signature']}")
    return "\n".join(lines)


def _format_resource_block(method):
    """
    If the method is file-dependent, scans TEST_RESOURCES_DIR and returns a
    prompt section listing available real test resource files.
    Returns an empty string if the method does not take file parameters.
    """
    if not is_file_dependent(method):
        return ""
    resources = scan_test_resources(config.TEST_RESOURCES_DIR)
    if not resources:
        return ""
    lines = [
        "## AVAILABLE TEST RESOURCE FILES",
        "The following real test files are available in src/test/resources/ and are",
        "guaranteed to be valid and parseable by the project under test:",
        "",
    ]
    for ext in sorted(resources):
        filenames = ", ".join(sorted(resources[ext]))
        lines.append(f"{ext} files: {filenames}")
    lines += [
        "",
        "Access them in tests using:",
        '    new File(getClass().getClassLoader().getResource("FILENAME").toURI())',
        "",
        "RULES:",
        "- Always use one of these files for happy path tests that require a valid file input.",
        "- Never construct file content programmatically for happy path tests.",
        "- Never use File.createTempFile() for happy path tests — temp files are empty and will fail format parsing.",
        "- File.createTempFile() is only acceptable when explicitly testing the empty-file or corrupt-file error case.",
        "- Never hardcode placeholder paths like \"path/to/file.fdf\" or \"validDocument.fdf\".",
        "- Always include java.net.URISyntaxException in PLANNED IMPORTS when using getResource(). Never include java.net.URL — it is not needed with the single-line pattern.",
        "- When accessing test resources, always use the single-line pattern: new File(getClass().getClassLoader().getResource(\"FILENAME\").toURI()). Never declare a URL variable — this avoids needing to import java.net.URL.",
    ]
    return "\n".join(lines)


def _format_class_inventory_section(method, class_inventory):
    """
    Builds the AVAILABLE PROJECT CLASSES section for the planning prompt.

    For every class referenced in source_file_imports that exists in
    class_inventory, this shows:
      - whether it is abstract / an interface
      - its public/protected constructors (so the LLM knows how to instantiate it)
      - factory methods (static builders)
      - concrete subclasses (so the LLM can pick one when the class is abstract)

    This is the primary fix for Flaw 5: the LLM previously removed tests when
    a dependency class had no entries in dependency_signatures.  With this
    section it can still legally construct those objects.
    """
    if not class_inventory:
        return ""

    imports = method.get('source_file_imports', [])
    if not imports:
        return ""

    # Extract simple class names from import lines like "import a.b.ClassName;"
    import_pattern = re.compile(r'import\s+([\w.]+);')
    lines = []

    for imp in imports:
        m = import_pattern.search(imp)
        if not m:
            continue
        full_name = m.group(1)
        entry = class_inventory.get(full_name)
        if not entry:
            continue

        class_name    = entry.get('class_name', full_name.split('.')[-1])
        is_abstract   = entry.get('is_abstract', False)
        is_interface  = entry.get('is_interface', False)
        constructors  = entry.get('constructors', [])
        factory_meths = entry.get('factory_methods', [])
        pub_methods   = entry.get('public_methods', [])
        subclasses    = entry.get('concrete_subclasses', [])

        kind = "interface" if is_interface else ("abstract class" if is_abstract else "class")
        lines.append(f"  {class_name} [{kind}]")

        # Public/protected constructors
        public_ctors = [c for c in constructors if c.get('visibility') in ('public', 'protected')]
        if public_ctors:
            for c in public_ctors:
                params = ', '.join(c.get('params', [])) or 'no args'
                lines.append(f"    constructor ({params}) [{c.get('visibility')}]")
        else:
            lines.append(f"    constructor — none publicly accessible")

        # Factory methods
        for fm in factory_meths:
            params = ', '.join(fm.get('params', [])) or 'no args'
            lines.append(f"    factory {fm['name']}({params}) → {fm.get('returns', '?')}")

        # Public methods — instance methods first, capped at 25 total to limit prompt size
        if pub_methods:
            instance_methods = [s for s in pub_methods if not s.startswith('static ')]
            static_methods   = [s for s in pub_methods if s.startswith('static ')]
            shown = (instance_methods + static_methods)[:25]
            lines.append(f"    public methods:")
            chunk_size = 4
            for i in range(0, len(shown), chunk_size):
                chunk = shown[i:i + chunk_size]
                lines.append(f"      {', '.join(chunk)}")
            if len(pub_methods) > 25:
                lines.append(f"      ... and {len(pub_methods) - 25} more")

        # Concrete subclasses (useful when the class is abstract/interface)
        if subclasses:
            lines.append(f"    concrete subclasses: {', '.join(subclasses)}")

        lines.append("")

    if not lines:
        return ""

    header = [
        "## AVAILABLE PROJECT CLASSES",
        "The following classes from SOURCE FILE IMPORTS are part of the project.",
        "Use this section to understand how to construct them.",
        "If a class is abstract or an interface, use one of its listed concrete subclasses.",
        "You MAY call any public method of these classes in your plan — you are NOT",
        "restricted to only the methods in DEPENDENCY SIGNATURES for these classes.",
        "",
    ]
    return "\n".join(header + lines)


_SKIP_STRATEGIES = {'skip', 'unknown', 'unresolvable_abstract', 'private_constructor'}


def _format_construction_section(dep_chain):
    """
    Formats the HOW TO CONSTRUCT EACH INPUT section from a dep_chain entry.
    Skips params with unresolvable strategies and notes them as TODOs.
    Returns empty string if dep_chain is None or has no params/receiver.
    """
    if not dep_chain:
        return ""

    lines = [
        "## HOW TO CONSTRUCT EACH INPUT",
        "Use these exact construction statements in your plan. Do not invent alternatives.",
        "",
    ]

    receiver = dep_chain.get('receiver')
    if receiver:
        if receiver.get('strategy') in _SKIP_STRATEGIES:
            lines.append("Receiver (declaring class instance):")
            lines.append("  // TODO: receiver could not be resolved — do not guess construction code.")
        else:
            lines.append("Receiver (declaring class instance):")
            lines.append(f"  {receiver.get('construction', '')}")
        lines.append("")

    params = dep_chain.get('params') or []
    for i, p in enumerate(params, 1):
        strategy = p.get('strategy', '')
        if strategy == 'skip':
            continue  # output-type param — exclude entirely
        lines.append(f"Parameter {i}: {p.get('type', '')} {p.get('name', '')}")
        if strategy in _SKIP_STRATEGIES:
            lines.append(f"  // TODO: parameter could not be resolved — do not invent construction code for it.")
        else:
            lines.append(f"  Strategy: {strategy}")
            lines.append(f"  Construction: {p.get('construction', '')}")
        lines.append("")

    if len(lines) <= 3:
        return ""  # nothing useful was added
    return "\n".join(lines)


def _format_caller_snippets(caller_snippets):
    """
    Formats the REAL USAGE EXAMPLES section from a list of caller dicts.
    Returns empty string if list is empty.
    """
    if not caller_snippets:
        return ""

    lines = [
        "## REAL USAGE EXAMPLES FROM CODEBASE",
        "The following snippets show how this method is actually called in the codebase.",
        "Use these as reference for realistic test inputs and assertions.",
        "",
    ]
    for i, caller in enumerate(caller_snippets, 1):
        longname = caller.get('caller_longname', 'unknown')
        line_no  = caller.get('caller_line', '?')
        snippet  = caller.get('snippet', '')
        lines.append(f"Example {i} (from {longname}, line {line_no}):")
        lines.append(snippet)
        lines.append("")

    return "\n".join(lines)


def _format_abstract_receiver_hint(method, dep_chain, class_inventory):
    """
    When the receiver (the class under test) is abstract and cannot be
    constructed directly, look it up in class_inventory and surface its
    known concrete subclasses so the LLM can pick one instead of falling
    back to null or a completely different class.
    """
    if not dep_chain or not class_inventory:
        return ""

    receiver = dep_chain.get('receiver', {})
    if not receiver:
        return ""
    if receiver.get('strategy') not in ('unresolvable_abstract', 'private_constructor', 'unknown'):
        return ""

    class_name = method.get('class_name', '')
    # Try to find the full class name from the method's full_name
    # full_name is like "org.apache.pdfbox.contentstream.PDFStreamEngine.processPage"
    parts = method.get('full_name', '').split('.')
    # The class is everything except the last segment (method name)
    full_class = '.'.join(parts[:-1]) if len(parts) > 1 else ''
    entry = class_inventory.get(full_class)
    if not entry:
        return ""

    subclasses = entry.get('concrete_subclasses', [])
    if not subclasses:
        return (
            f"## RECEIVER CONSTRUCTION NOTE\n"
            f"{class_name} is abstract and has no known concrete subclasses in this project.\n"
            f"Do not instantiate it and do not substitute a different class.\n"
            f"Write a TODO comment for the receiver and plan what assertions would be made if it could be constructed.\n"
        )

    return (
        f"## RECEIVER CONSTRUCTION NOTE\n"
        f"{class_name} is abstract and cannot be instantiated directly.\n"
        f"Use one of these known concrete subclasses as the receiver — "
        f"it inherits the method under test:\n"
        f"  {', '.join(subclasses)}\n"
        f"Pick the simplest one to construct from AVAILABLE PROJECT CLASSES above.\n"
    )


def build_planning_prompt(method, dep_chain=None, caller_snippets=None, class_inventory=None):
    """
    Step 1 of 2: asks the LLM to produce a structured test plan (no code).
    The plan includes the exact imports needed and the exact method calls per test.
    """
    import_section = _format_imports(method)
    dep_section = _format_dependency_signatures(method)
    resource_block = _format_resource_block(method)

    prompt = f"""You are an expert Java software engineer. Your task is to produce a structured TEST PLAN for a JUnit 5 test class. Do NOT write any Java code — output only the plan as described below.

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}
"""

    if method.get('javadoc'):
        prompt += f"Javadoc: {method['javadoc']}\n"

    prompt += f"""
Implementation:
{method.get('body', '')}

"""

    if import_section:
        prompt += import_section + "\n\n"

    if dep_section:
        prompt += dep_section + "\n\n"

    inventory_section = _format_class_inventory_section(method, class_inventory or {})
    if inventory_section:
        prompt += inventory_section + "\n\n"

    if resource_block:
        prompt += resource_block + "\n\n"

    construction_section = _format_construction_section(dep_chain)
    if construction_section:
        prompt += construction_section + "\n\n"

    abstract_hint = _format_abstract_receiver_hint(method, dep_chain, class_inventory or {})
    if abstract_hint:
        prompt += abstract_hint + "\n\n"

    caller_section = _format_caller_snippets(caller_snippets)
    if caller_section:
        prompt += caller_section + "\n\n"

    prompt += f"""=== BRANCH ANALYSIS (complete this before planning tests) ===
Before listing test methods, you MUST enumerate every branch in the implementation above.
A branch is any point where execution can take two or more different paths:
  - if / else if / else blocks
  - switch cases (including default)
  - ternary expressions (condition ? a : b)
  - try / catch / finally blocks (the normal path AND each catch block)
  - loops where the body may or may not execute (empty collection / null guard)
  - early return or throw statements guarded by a condition

For each branch write one line:
  Branch N: <condition or construct> → <true/taken path summary> | <false/not-taken path summary>

Every branch you list MUST have at least one test method mapped to it in TEST METHODS below.
Every test method MUST state which branch(es) it covers in a "Covers branches:" field.

=== HARD RULES ===
--- Method calling ---
1a. For classes in DEPENDENCY SIGNATURES: ONLY call the methods listed verbatim there. No other methods on those classes.
1b. For classes in AVAILABLE PROJECT CLASSES (not in DEPENDENCY SIGNATURES): you may call any method shown in their "public methods" list. Do not invent method names not listed there.
1c. For any other class: do not call instance or static methods on it at all.
2. Write each planned method call as ClassName.methodName(ParamType1, ParamType2) so it is unambiguous.

--- Imports ---
3. For PLANNED IMPORTS, ONLY list imports that appear in SOURCE FILE IMPORTS, plus JUnit 5 (org.junit.jupiter.api.*) and Java SE (java.*) classes. Do NOT invent or add any other import. Always include java.net.URISyntaxException when using getResource(). Never include java.net.URL.
4. Do NOT plan to use Mockito or any mocking framework.

--- Object construction ---
5. Do NOT plan to access private fields or methods.
6. NEVER plan to pass bare null to an overloaded method — always plan to cast it (e.g. (File) null).
7. You MUST use the exact construction statements provided in HOW TO CONSTRUCT EACH INPUT. Do not invent, simplify, or replace them. If a parameter has no construction provided, write a TODO comment for it and do not guess.
8. NEVER use null as a substitute for a required object. Null is only acceptable when testing explicit null-handling behaviour documented by the method. If an object cannot be constructed, write a TODO comment — do not substitute null.
9. The class under test is `{method['class_name']}`. NEVER substitute a different class as the receiver. If it is abstract, use a concrete subclass from RECEIVER CONSTRUCTION NOTE or AVAILABLE PROJECT CLASSES, but declare the variable as type `{method['class_name']}`. If it cannot be constructed at all, omit the test entirely.

--- Test design ---
10. Never plan chained method calls. Every method return value must be assigned to a named variable before calling methods on it.
11. Every branch listed in BRANCH ANALYSIS must be covered by at least one test method. Do not omit any branch.
12. Plan meaningful assertions: use assertEquals/assertSame to check exact return values, assertThrows to check exceptions, assertTrue/assertFalse only for boolean results. assertNotNull alone is not a meaningful assertion — always additionally verify the content or state of the returned object.
13. Never plan assertThrows(IOException.class, () -> method(null)). Passing null to a method that dereferences it throws NullPointerException, not a checked exception.

--- Files ---
14. Never use File.createTempFile() for happy path tests. Always use a real resource file from AVAILABLE TEST RESOURCE FILES.
15. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource("FILENAME").toURI() with a filename from AVAILABLE TEST RESOURCE FILES.
16. If AVAILABLE TEST RESOURCE FILES lists any files, at least one happy-path test MUST load a real resource file from that list.

--- Output hygiene ---
17. NEVER write assertTrue(true) or leave a @Test method body empty. If a test scenario cannot be implemented, omit that test method entirely.
18. NEVER define a class, interface, or enum inside the test file. The file must contain exactly one type: the test class. Do NOT recreate any production class from scratch.

=== REQUIRED OUTPUT FORMAT ===
Output exactly the following structure. Fill in each section — do not skip any.

BRANCH ANALYSIS:
Branch 1: <condition> → <taken path> | <not-taken path>
Branch 2: ...
(list every branch)

PLANNED IMPORTS:
- <exact import statement>
- <exact import statement>
...

TEST METHODS:
1. <camelCase test method name>
   Scenario: <one sentence describing what this test verifies>
   Covers branches: <Branch N, Branch M, ...>
   Setup: <objects to instantiate and how, or "none">
   Method calls: <ClassName.methodName(ParamType), ...>
   Assertions: <exact assertion type and what is being checked>

2. <camelCase test method name>
   Scenario: ...
   Covers branches: ...
   Setup: ...
   Method calls: ...
   Assertions: ...

(one test method per branch minimum; consolidate only when two branches are exercised by identical setup)

BRANCH COVERAGE SUMMARY:
- Branch 1: covered by test <name>
- Branch 2: covered by test <name>
...
(every branch from BRANCH ANALYSIS must appear here)

Output the plan now:"""

    return prompt


def build_generation_from_plan_prompt(method, plan):
    """
    Step 2 of 2: asks the LLM to generate the test class by implementing the plan exactly.
    Imports are restricted to exactly what was listed in the plan.
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    resource_block = _format_resource_block(method)

    prompt = f"""You are an expert Java software engineer. Implement the following test plan as a complete, compilable JUnit 5 test class.

=== TEST PLAN ===
{plan}

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}

"""

    if resource_block:
        prompt += resource_block + "\n\n"

    prompt += f"""=== HARD RULES ===
--- Structure ---
1. Test class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. Implement EXACTLY the test methods listed in the plan — same names, same method calls, same assertions. Do NOT add, remove, or rename any test method.
4. ONLY add import statements listed verbatim in the PLANNED IMPORTS section of the plan. Do NOT add any import not in that list.
5. NEVER define a class, interface, or enum inside the test file. The file must contain exactly one type: `{test_class_name}`. Do NOT recreate or redefine any production class.
6. The class under test is `{method['class_name']}`. It MUST appear in the test code as a type, constructor call, or static reference. If the plan uses a concrete subclass, declare the variable as type `{method['class_name']}` (e.g. `{method['class_name']} obj = new ConcreteSubclass(...)`).

--- Implementation ---
7. Use the exact construction code from the plan setup section verbatim. Do not rewrite, simplify, or replace it.
8. Never chain method calls. Always assign return values to named variables before calling methods on them.
9. NEVER pass bare null to an overloaded method — cast it as specified in the plan.
10. Do NOT access private fields or methods.
11. Do NOT use Mockito or any mocking framework.
12. If the method throws a checked exception, declare `throws <ExceptionType>` on the test method rather than wrapping in try-catch, unless the plan uses assertThrows.
13. Never pass a raw InputStream or FileInputStream where a wrapper type is required.

--- Assertions ---
14. Write meaningful assertions: use assertEquals/assertSame for exact values, assertThrows for exceptions, assertTrue/assertFalse only for booleans. assertNotNull alone is not sufficient — also verify the content or state of the returned object.
15. Never write assertThrows(IOException.class, () -> method(null)). Null dereferences throw NullPointerException, not checked exceptions.
16. NEVER write assertTrue(true) or leave a @Test method body empty. If a test scenario cannot be implemented, omit that test method entirely.

--- Files ---
17. Never construct file content programmatically for happy path tests. Use the resource files specified in the plan.
18. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource() as specified in the plan.
19. If AVAILABLE TEST RESOURCE FILES is present, every file-input test MUST use getClass().getClassLoader().getResource("FILENAME").toURI() with a filename from that list.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code. No explanations, no markdown fences.
The output must start with the package declaration.

Generate the test class now:"""

    return prompt


def _format_inventory_for_retry(method, class_inventory):
    """
    Compact version of the inventory section for retry/violation prompts.
    Shows only class name, constructors, and method names (no verbose descriptions).
    """
    if not class_inventory:
        return ""
    import_pattern = re.compile(r'import\s+([\w.]+);')
    lines = ["=== AVAILABLE PROJECT CLASSES (public methods you may call) ==="]
    found = False
    for imp in method.get('source_file_imports', []):
        m = import_pattern.search(imp)
        if not m:
            continue
        entry = class_inventory.get(m.group(1))
        if not entry:
            continue
        found = True
        class_name = entry.get('class_name', m.group(1).split('.')[-1])
        pub = entry.get('public_methods', [])
        ctors = [c for c in entry.get('constructors', []) if c.get('visibility') in ('public', 'protected')]
        ctor_str = '; '.join(f"new {class_name}({', '.join(c['params'])})" for c in ctors) or 'no public constructor'
        method_names = ', '.join(
            re.search(r'\b([A-Za-z_]\w*)\s*\(', s).group(1)
            for s in pub
            if re.search(r'\b([A-Za-z_]\w*)\s*\(', s)
        ) or 'none'
        lines.append(f"  {class_name}: constructors [{ctor_str}] | methods: {method_names}")
    return "\n".join(lines) + "\n" if found else ""


def build_retry_prompt(error_message, failing_test, method, class_inventory=None):
    """
    Builds the retry prompt when the generated test fails.

    Args:
        error_message: compiler or runtime error string
        failing_test: the generated test code that failed
        method: method metadata dict
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    import_section = _format_imports(method)
    dep_section = _format_dependency_signatures(method)

    prompt = f"""The JUnit 5 test you generated previously failed. Carefully read the error, identify the root cause, and produce a fully corrected version.

=== COMPILE / RUNTIME ERROR ===
{error_message}

=== FAILING TEST ===
{failing_test}

=== METHOD UNDER TEST ===
// Signature:
{method.get('signature', '')}

// Implementation:
{method.get('body', '')}

=== STRICT CONSTRAINTS ===
1. Class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. Fix ONLY the specific line the error points to. Do NOT rewrite parts that were correct. Do NOT introduce any new class or method that is not already present in the failing test.
4. For classes in ALLOWED DEPENDENCY SIGNATURES: only call methods listed there. For classes in AVAILABLE PROJECT CLASSES: only call methods shown in their "methods" list. Do not invent method names.
5. For every non-java.lang class you reference, add an explicit import. Copy the exact import line from SOURCE FILE IMPORTS. If a class is not listed there and is not a JUnit 5 / Java SE class, remove it from the test.
6. Do NOT use internal implementation classes — only use public API types needed to call the method and verify its return value.
7. NEVER pass bare null to any overloaded method. Always cast: `(File) null`, `(InputStream) null`, `(String) null`.
8. Do NOT access private fields or methods of any class.
9. Never chain method calls. Always assign return values to named variables before calling methods on them.
10. If a specific assertion cannot be written due to access constraints, omit that test method entirely. NEVER write assertTrue(true) or leave a test body empty — a test that always passes regardless of production code is worse than no test.

=== ERROR DIAGNOSIS GUIDE ===
- "reference to X is ambiguous" → you passed uncast null to an overloaded method. Cast it: (ExpectedType) null.
- "cannot find symbol: method X" → that method does not exist on that class. Check ALLOWED DEPENDENCY SIGNATURES.
- "cannot find symbol: class X" → missing import or class does not exist. Check SOURCE FILE IMPORTS.
- "cannot be instantiated" → the class is abstract. Do not instantiate it directly.
- "has private access" → you accessed a private member. Only use the public API.

=== OUTPUT FORMAT ===
Output ONLY the corrected raw Java source code, starting with the package declaration. No explanations.

"""

    if import_section:
        prompt += import_section + "\n\n"

    if dep_section:
        prompt += "=== ALLOWED DEPENDENCY SIGNATURES ===\n" + dep_section + "\n\n"

    inventory_section = _format_inventory_for_retry(method, class_inventory)
    if inventory_section:
        prompt += inventory_section + "\n"

    prompt += "\nGenerate the corrected test class now:"
    return prompt


def build_allowlist_violation_prompt(violations, generated_test, method):
    """
    Builds a prompt when the generated test calls methods that are not in
    dependency_signatures (hallucinated methods).

    Args:
        violations: list of "ClassName.methodName" strings that were hallucinated
        generated_test: the generated test code that failed the allowlist check
        method: method metadata dict
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    import_section = _format_imports(method)

    # Build full allowlist section from all dependency_signatures
    deps = method.get('dependency_signatures', [])
    by_class = {}
    for d in deps:
        by_class.setdefault(d['class_name'], []).append(d)

    allowlist_lines = ["// ALLOWED DEPENDENCY SIGNATURES (use ONLY these — no other methods):"]
    for cls, sigs in by_class.items():
        allowlist_lines.append(f"//   Class: {cls}")
        for d in sigs:
            allowlist_lines.append(f"//     [{d['kind']}] {d['signature']}")
    allowlist_section = "\n".join(allowlist_lines)

    violations_str = "\n".join(f"  - {v}" for v in violations)

    prompt = f"""The test you generated calls methods that do NOT exist in the allowed dependency signatures. These hallucinated methods will cause compilation or runtime failures.

=== HALLUCINATED METHODS (these do not exist — remove or replace them) ===
{violations_str}

=== GENERATED TEST (contains hallucinated calls) ===
{generated_test}

=== METHOD UNDER TEST ===
// Signature:
{method.get('signature', '')}

// Implementation:
{method.get('body', '')}

=== STRICT CONSTRAINTS ===
1. Class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. You MUST NOT call any method listed under HALLUCINATED METHODS above.
4. You MUST ONLY call methods explicitly listed in ALLOWED DEPENDENCY SIGNATURES below.
5. Do NOT invent method names, parameter types, or constructors — use only what is in the allowlist.
6. For every non-java.lang class you reference, add an explicit import from SOURCE FILE IMPORTS.
7. NEVER pass bare null to any overloaded method — always cast it (e.g. (File) null).
8. Do NOT access private fields or methods.

=== OUTPUT FORMAT ===
Output ONLY the corrected raw Java source code, starting with the package declaration. No explanations.

"""

    if import_section:
        prompt += import_section + "\n\n"

    if allowlist_section:
        prompt += allowlist_section + "\n\n"

    prompt += "\nGenerate the corrected test class now:"
    return prompt