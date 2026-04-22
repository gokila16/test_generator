import re
import config
from src.behavioral_analyzer import extract_behavioral_constraints
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


def _annotate_resource_entry(entry):
    """
    Returns the display string for a single resource entry dict.

    - Plain file (no encryption metadata, or not encrypted):
        "filename.pdf"
    - Encrypted with known password:
        "filename.pdf (encrypted, password: "userpassword")"
    - Encrypted with unknown password:
        "filename.pdf (encrypted, password unknown — do not use for happy-path tests)"
    """
    rel_path = entry['rel_path']
    is_encrypted = entry.get('is_encrypted', False)
    if not is_encrypted:
        return rel_path
    password = entry.get('password')
    if password is not None:
        return f'{rel_path} (encrypted, password: "{password}")'
    return f"{rel_path} (encrypted, password unknown — do not use for happy-path tests)"


def _format_resource_block(method):
    """
    If the method is file-dependent, scans TEST_RESOURCES_DIR and returns a
    prompt section listing available real test resource files.
    Returns an empty string if the method does not take file parameters.

    Encrypted files are annotated so the LLM knows which password to supply
    (or to skip the file for happy-path tests if the password is unknown).
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
        entries = resources[ext]
        annotated = ", ".join(
            _annotate_resource_entry(e)
            for e in sorted(entries, key=lambda e: e['rel_path'])
        )
        lines.append(f"{ext} files: {annotated}")
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


def build_planning_prompt(method, dep_chain=None, caller_snippets=None, class_inventory=None, testable_slices=None):
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

    behavioral = extract_behavioral_constraints(method.get('body', ''))
    behavioral_lines = []
    for t in behavioral.get('throws', []):
        if t['condition'] is not None:
            behavioral_lines.append(
                f"- This method throws {t['exception']} only when "
                f"({t['condition']}). "
                f"Do not assert assertThrows({t['exception']}.class) for any other input."
            )
    for r in behavioral.get('returns', []):
        behavioral_lines.append(
            f"- On the branch where `{r['context']}`, this method returns "
            f"{r['value']}. Use this exact value in your assertEquals assertion."
        )
    if behavioral_lines:
        prompt += "## BEHAVIORAL CONSTRAINTS\n"
        prompt += "The following behaviors are directly readable from the implementation.\n"
        prompt += "Use them as precise contracts — do not guess or infer different values.\n\n"
        prompt += "\n".join(behavioral_lines) + "\n\n"

    ternary_slices = [
        s for s in (testable_slices or [])
        if s.get('slice_type') == 'RETURN_SLICE'
        and ('is true' in s.get('entry_condition', '')
             or 'is false' in s.get('entry_condition', ''))
    ]
    if ternary_slices:
        prompt += "## TERNARY RETURN PATHS\n"
        prompt += (
            "The following ternary branches were detected statically. Each is a\n"
            "distinct return path that requires its own test method.\n\n"
        )
        for s in ternary_slices:
            prompt += f"- When {s['entry_condition']}: {s['expected_observable']}\n"
        prompt += (
            "\nThese are not guesses — they are read directly from the source.\n"
            "Treat each line as a BEHAVIORAL CONSTRAINT with the same authority\n"
            "as the THROW conditions above.\n\n"
        )

    prompt += """=== STEP 0: ADVERSARIAL ANALYSIS (complete before anything else) ===
You are not trying to demonstrate that this method works.
You are trying to find inputs that make it produce wrong results,
throw unexpected exceptions, or silently corrupt state.

Answer all four questions before proceeding:

1. TRUST BOUNDARIES
   Which parameters does this method use without validating first?
   (No null check, no range check, no type check before use.)
   These are your highest-priority test targets.

2. HIDDEN ASSUMPTIONS
   What must be true about the object's internal state before this
   method is called for it to behave correctly?
   What happens if those preconditions are violated?

3. BOUNDARY VALUES
   For every parameter, state the boundary values you will test:
   - numeric: 0, -1, 1, Integer.MAX_VALUE, Integer.MIN_VALUE
   - String: null, "", " ", very long string
   - Collection/array: null, empty, single element, large size
   - Object: null, default-constructed, partially initialized
   State explicitly which boundary is most likely to expose a bug
   and why.

4. MUTATION RISK
   Does this method modify any of its input parameters or shared
   state beyond what the return value suggests?
   If yes, name the mutated target and its expected state after
   the call — this becomes an assertion requirement.

Format your answers as:
TRUST BOUNDARIES: <list each unvalidated parameter>
HIDDEN ASSUMPTIONS: <list each precondition>
BOUNDARY VALUES: <param name> → <values to test> → <most dangerous>
MUTATION RISK: <field or param> → <state after call>

=== STEP 0.5: INPUT DOMAIN PARTITIONING ===
For each parameter identified in STEP 0, define its equivalence
partitions. A partition is a set of inputs the method treats
identically — one representative per partition is sufficient.

Format:
  Parameter <name>:
    Valid partition:    <representative value> — <why it is valid>
    Invalid partition:  <representative value> — <why it is invalid>
    Boundary partition: <representative value> — <the edge case>

Choose the most dangerous representative for each partition —
the value most likely to expose a bug if the method mishandles it.

These partitions become test methods IN ADDITION to the
slice-derived tests. Every invalid partition that is reachable
via public API must have a corresponding test method.

"""

    prompt += f"""=== STEP 1: UNDERSTAND THE METHOD (mandatory — complete before writing any test) ===
You are acting as a senior SDET who has just been handed this method to test.
Before planning a single test case, read the implementation above and answer:

1. PURPOSE
   What does this method DO? State the computation, transformation, or validation it performs
   in one plain-English sentence. Do not describe the code structure — describe the INTENT.

2. INPUT → OUTPUT MAP
   For every distinct execution path, state the EXACT result. Use values directly visible in
   the implementation. Format: "When [input condition] → returns [exact value] / throws [ExceptionType]"
   - Be specific: "returns the parsed document with N pages" not "returns an object"
   - If it returns a boolean, state which condition produces true vs false
   - If it returns a string or number, quote the exact literal from the source
   - If it returns an object, name which fields/properties are populated and with what

3. ASSERTION DERIVATION
   Given the exact construction statements in HOW TO CONSTRUCT EACH INPUT above, trace through
   the method body step by step and state what value the method will return (or throw) for each
   input configuration. This is the value you MUST use in your assertEquals/assertThrows.
   Format: "With [constructed input description] → assertEquals([exact expected value], result)"
   or      "With [constructed input description] → assertThrows([ExceptionType].class, ...)"
   Do NOT write "assertNotNull(result)" — derive the actual expected value.

=== STEP 2: BRANCH ANALYSIS (complete after Step 1) ===
Enumerate every branch in the implementation. A branch is any point where execution takes
different paths: if/else, switch, ternary, try/catch, loops that may not execute, early returns.

For each branch write one line:
  Branch N: <condition or construct> → <taken path result> | <not-taken path result>

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
11. Every branch listed in STEP 2 BRANCH ANALYSIS must be covered by at least one test method. Do not omit any branch.
12. Assertions MUST be derived from your STEP 1 ASSERTION DERIVATION above — not guessed.
    - assertEquals: use the EXACT value you traced in Step 1 for the given input
    - assertThrows: only when Step 1 shows the method throws for that specific input
    - assertTrue/assertFalse: only for boolean returns — state which is correct from Step 1
    - For object returns: call getter/accessor methods on the result to verify specific field values, not just assertNotNull
    - assertNotNull alone is NEVER acceptable — always pair it with at least one assertion on content or state
    - Do NOT write an assertion you cannot justify from reading the method body

--- Files ---
13. Never use File.createTempFile() for happy path tests. Always use a real resource file from AVAILABLE TEST RESOURCE FILES.
14. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource("FILENAME").toURI() with a filename from AVAILABLE TEST RESOURCE FILES.
15. If AVAILABLE TEST RESOURCE FILES lists any files, at least one happy-path test MUST load a real resource file from that list.

--- Output hygiene ---
16. NEVER write assertTrue(true) or leave a @Test method body empty. If a test scenario cannot be implemented, omit that test method entirely.
17. The file must contain exactly one TOP-LEVEL type: the test class. Simple helper types (e.g. a small data holder needed only for this test) may be declared as `static` nested classes INSIDE the test class body. Do NOT define additional top-level classes, interfaces, or enums. Do NOT give any nested class the same name as a production class.

--- Ternary paths ---
18. For every line in TERNARY RETURN PATHS, there must be exactly one test method that exercises that branch. The assertion must use the expected_observable from that line verbatim. Do not merge the true-branch and false-branch tests into one method.

--- Adversarial tests ---
19. Every TRUST BOUNDARY identified in STEP 0 must have at least
    one test that passes that parameter unvalidated (e.g. null,
    negative, empty). If the method does not guard it, the test
    must assert the resulting exception or corrupted state —
    not assertDoesNotThrow.
20. Every BOUNDARY VALUE identified in STEP 0 must appear as the
    input in at least one test method. Do not test only the
    happy-path representative. The boundary representative is
    often more valuable than the valid one.

=== REQUIRED OUTPUT FORMAT ===
Output exactly the following structure. Fill in each section — do not skip any.

METHOD SEMANTICS:
Purpose: <one sentence stating what the method computes, transforms, or validates>
Input → Output map:
  - When <condition>: returns <exact value> / throws <ExceptionType>
  - When <condition>: returns <exact value> / throws <ExceptionType>
  (list every distinct path)
Assertion derivation:
  - With <constructed input description>: assertEquals(<exact expected value>, result)
  - With <constructed input description>: assertThrows(<ExceptionType>.class, ...)
  (one line per test scenario — this drives every assertion in TEST METHODS)

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
   Assertions: <exact assertion with the value derived in METHOD SEMANTICS above>

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

    prompt = f"""You are an expert Java software engineer acting as a senior SDET. Implement the following test plan as a complete, compilable JUnit 5 test class.

Before writing any @Test method, find the METHOD SEMANTICS section in the plan above. Copy the exact value from "Assertion derivation" for that test's input. That value is your assertion. Do not derive it again.
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
5. The file must contain exactly one TOP-LEVEL type: `{test_class_name}`. Simple helper types (e.g. a small data holder needed only for this test) may be declared as `static` nested classes INSIDE `{test_class_name}`. Do NOT define additional top-level classes, interfaces, or enums. Do NOT give any nested class the same name as a production class.
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
14. Every assertion MUST use the exact expected value from METHOD SEMANTICS in the plan.
    - assertEquals: use the exact value stated in "Assertion derivation" for this test's input
    - For object returns: call getter/accessor methods on the result to verify specific field values
    - assertNotNull alone is NEVER sufficient — always follow it with at least one content/state assertion
    - assertThrows: only when METHOD SEMANTICS explicitly shows the method throws for that input
    - Do NOT write an assertion you cannot justify from the plan's METHOD SEMANTICS section
15. Never write assertThrows(IOException.class, () -> method(null)). Null dereferences throw NullPointerException, not checked exceptions.
16. NEVER write assertTrue(true) or leave a @Test method body empty. If a test scenario cannot be implemented, omit that test method entirely.

--- Files ---
17. Never construct file content programmatically for happy path tests. Use the resource files specified in the plan.
18. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource() as specified in the plan.
19. If AVAILABLE TEST RESOURCE FILES is present, every file-input test MUST use getClass().getClassLoader().getResource("FILENAME").toURI() with a filename from that list.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code. No explanations, no markdown fences.
The output must start with the package declaration.

Generate the test class now:
```java
package"""

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


_TYPE_ERROR_SIGNALS = (
    "incompatible types",
    "no suitable constructor",
    "cannot find symbol: constructor",
    "cannot be applied to",
)


def _classify_error(error_message: str) -> str:
    """
    Returns "type_error" if *error_message* contains any of the known C2/C5
    type-or-constructor error signals; otherwise returns "other".
    """
    if not error_message:
        return "other"
    lower = error_message.lower()
    if any(signal in lower for signal in _TYPE_ERROR_SIGNALS):
        return "type_error"
    return "other"


def build_retry_prompt(error_message, failing_test, method,
                       class_inventory=None, dep_chain=None):
    """
    Builds the retry prompt when the generated test fails.

    Args:
        error_message: compiler or runtime error string
        failing_test: the generated test code that failed
        method: method metadata dict
        class_inventory: optional class inventory dict
        dep_chain: optional dep_chain entry for construction guidance
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    import_section = _format_imports(method)
    dep_section = _format_dependency_signatures(method)

    error_kind = _classify_error(error_message)
    if error_kind == "type_error":
        constraint_3 = (
            "3. For this type or constructor error, rewrite the entire declaration "
            "and construction block for the affected variable. "
            "Do not change any other test method or any line unrelated to the failing variable."
        )
    else:
        constraint_3 = (
            "3. Fix ONLY the specific line the error points to. "
            "Do NOT rewrite parts that were correct. "
            "Do NOT introduce any new class or method that is not already present in the failing test."
        )

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
{constraint_3}
4. For classes in ALLOWED DEPENDENCY SIGNATURES: only call methods listed there. For classes in AVAILABLE PROJECT CLASSES: only call methods shown in their "methods" list. Do not invent method names.
5. For every non-java.lang class you reference, add an explicit import. Copy the exact import line from SOURCE FILE IMPORTS. If a class is not listed there and is not a JUnit 5 / Java SE class, remove it from the test.
6. Do NOT use internal implementation classes — only use public API types needed to call the method and verify its return value.
7. NEVER pass bare null to any overloaded method. Always cast: `(File) null`, `(InputStream) null`, `(String) null`.
8. Do NOT access private fields or methods of any class.
9. Never chain method calls. Always assign return values to named variables before calling methods on them.
10. If a specific assertion cannot be written due to access constraints, omit that test method entirely. NEVER write assertTrue(true) or leave a test body empty — a test that always passes regardless of production code is worse than no test.
11. The file must contain exactly one TOP-LEVEL type: `{test_class_name}`. Helper types may be `static` nested classes INSIDE `{test_class_name}` but must not share a name with any production class. Do NOT add extra top-level classes.

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

    construction_section = _format_construction_section(dep_chain)
    if construction_section:
        prompt += "=== HOW TO CONSTRUCT EACH INPUT ===\n" + construction_section + "\n\n"

    inventory_section = _format_inventory_for_retry(method, class_inventory)
    if inventory_section:
        prompt += inventory_section + "\n"

    prompt += "\nGenerate the corrected test class now:"
    return prompt


def _parse_violation(v: str):
    """
    Returns a (kind, data) tuple for a violation string.

    kind == 'HALLUCINATED_METHOD': data is the plain "ClassName.methodName" string.
    kind == 'TYPE_MISMATCH':       data is a dict with keys:
        qualified    – "ClassName.methodName"
        n_args       – int, number of args actually passed
        overloads    – list[list[str]], known parameter-type lists
    """
    if v.startswith("TYPE_MISMATCH::"):
        parts = v.split("::")
        if len(parts) >= 4:
            qualified = parts[1]
            n_args_str = parts[2]
            overloads_raw = parts[3]
            overloads = [
                [t for t in ol.split(',') if t] if ol else []
                for ol in overloads_raw.split('||')
            ]
            return 'TYPE_MISMATCH', {
                'qualified': qualified,
                'n_args': int(n_args_str) if n_args_str.isdigit() else n_args_str,
                'overloads': overloads,
            }
    if v.startswith("HALLUCINATED_IMPORT::"):
        return 'HALLUCINATED_IMPORT', v.split("::", 1)[1]
    return 'HALLUCINATED_METHOD', v


def build_allowlist_violation_prompt(violations, generated_test, method,
                                     dep_chain=None, class_inventory=None):
    """
    Builds a prompt when the generated test has allowlist violations.

    Handles two violation kinds:
      - HALLUCINATED_METHOD ("ClassName.methodName") — method does not exist.
      - TYPE_MISMATCH ("TYPE_MISMATCH::..." encoded string) — method exists but
        was called with the wrong number of arguments.

    Args:
        violations: list of violation strings from check_against_allowlist
        generated_test: the generated test code that failed the allowlist check
        method: method metadata dict
        dep_chain: optional dep_chain entry (passed through to _format_construction_section)
        class_inventory: optional class inventory dict (passed through to _format_inventory_for_retry)
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

    # Separate violations into the three kinds
    hallucinated = []
    type_mismatches = []
    import_violations = []
    for v in violations:
        kind, data = _parse_violation(v)
        if kind == 'TYPE_MISMATCH':
            type_mismatches.append(data)
        elif kind == 'HALLUCINATED_IMPORT':
            import_violations.append(data)
        else:
            hallucinated.append(data)

    # Build the violations block
    violations_parts = []

    if hallucinated:
        hm_lines = ["=== HALLUCINATED METHODS (these do not exist — remove or replace them) ==="]
        for v in hallucinated:
            hm_lines.append(f"  - {v}")
        violations_parts.append("\n".join(hm_lines))

    if type_mismatches:
        tm_lines = ["=== WRONG ARGUMENT COUNT (method exists but called with wrong number of arguments) ==="]
        for tm in type_mismatches:
            qualified = tm['qualified']
            method_name = qualified.split('.')[-1]
            n_args = tm['n_args']
            overloads = tm['overloads']
            tm_lines.append(f"  Called: {qualified} with {n_args} args")
            tm_lines.append(f"  Known overloads:")
            for ol in overloads:
                if ol:
                    tm_lines.append(f"    - {method_name}({', '.join(ol)})")
                else:
                    tm_lines.append(f"    - {method_name}()")
        violations_parts.append("\n".join(tm_lines))

    if import_violations:
        iv_lines = [
            "=== HALLUCINATED IMPORTS (these classes do not exist "
            "in this project — remove them) ==="
        ]
        for fqn in import_violations:
            iv_lines.append(f"  - import {fqn};")
        iv_lines += [
            "",
            "Remove these imports and remove all code that depends on them.",
            "Replace with classes from SOURCE FILE IMPORTS only.",
        ]
        violations_parts.append("\n".join(iv_lines))

    violations_block = "\n\n".join(violations_parts)

    # Choose a header that accurately reflects the kinds of violations present
    if import_violations and not hallucinated and not type_mismatches:
        header = (
            "The test you generated imports classes that do not exist "
            "in this project. These will cause compile failures before "
            "any method call is reached."
        )
    elif import_violations:
        header = (
            "The test you generated has multiple problems: "
            + ("hallucinated methods, " if hallucinated else "")
            + ("wrong argument counts, " if type_mismatches else "")
            + "and imports that do not exist in this project."
        )
    elif hallucinated and type_mismatches:
        header = (
            "The test you generated has two kinds of problems: methods that do not exist "
            "(hallucinated), and methods called with the wrong number of arguments (type mismatch)."
        )
    elif hallucinated:
        header = (
            "The test you generated calls methods that do NOT exist in the allowed dependency "
            "signatures. These hallucinated methods will cause compilation or runtime failures."
        )
    else:
        header = (
            "The test you generated calls methods with the wrong number of arguments. "
            "Check the known overload signatures below and use the correct argument count."
        )

    constraint_3 = (
        "3. You MUST NOT call any method listed under HALLUCINATED METHODS above."
        if hallucinated else
        "3. You MUST NOT use the wrong number of arguments for any method call."
    )

    prompt = f"""{header}

{violations_block}

=== GENERATED TEST (contains violations) ===
{generated_test}

=== METHOD UNDER TEST ===
// Signature:
{method.get('signature', '')}

// Implementation:
{method.get('body', '')}

=== STRICT CONSTRAINTS ===
1. Class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
{constraint_3}
4. You MUST ONLY call methods explicitly listed in ALLOWED DEPENDENCY SIGNATURES below.
5. Do NOT invent method names, parameter types, or constructors — use only what is in the allowlist.
6. For every non-java.lang class you reference, add an explicit import from SOURCE FILE IMPORTS.
7. NEVER pass bare null to any overloaded method — always cast it (e.g. (File) null).
8. Do NOT access private fields or methods.
9. The file must contain exactly one TOP-LEVEL type: `{test_class_name}`. Helper types may be `static` nested classes INSIDE `{test_class_name}` but must not share a name with any production class. Do NOT add extra top-level classes.

=== OUTPUT FORMAT ===
Output ONLY the corrected raw Java source code, starting with the package declaration. No explanations.

"""

    if import_section:
        prompt += import_section + "\n\n"

    if allowlist_section:
        prompt += allowlist_section + "\n\n"

    construction_section = _format_construction_section(dep_chain)
    if construction_section:
        prompt += construction_section + "\n\n"

    inventory_section = _format_inventory_for_retry(method, class_inventory)
    if inventory_section:
        prompt += inventory_section + "\n\n"

    prompt += "\nGenerate the corrected test class now:"
    return prompt


# ---------------------------------------------------------------------------
# Recovery prompt
# ---------------------------------------------------------------------------

_RECOVERY_INSTRUCTIONS = {
    'no_code_block': (
        "Your response did not contain any recognizable Java code — it must start "
        "with a package declaration."
    ),
    'no_test_annotation': (
        "Your response contained no @Test annotations. Every test method must be "
        "annotated with @Test."
    ),
    'sut_missing': (
        "Your test does not reference the class under test `{class_name}` anywhere "
        "in the code body (imports alone do not count). It must appear as a type, "
        "constructor call, or static reference in at least one test method."
    ),
    'trivial_assertions': (
        "All your test methods contain only assertTrue(true) or have empty bodies. "
        "Write real assertions that verify actual return values or state changes. "
        "If a test scenario cannot be implemented, omit that test method entirely."
    ),
    'no_test_methods': (
        "Your response contains no @Test methods. Implement at least one method "
        "annotated with @Test."
    ),
}


def build_recovery_prompt(fail_reason, bad_response, method, plan):
    """
    Builds a targeted recovery prompt when extraction/post-processing rejected
    the generation response (Tier 1: no usable code; Tier 2: Maven won't catch it).

    Anchors on the already-accepted plan so the LLM re-implements rather than
    re-plans from scratch.

    Args:
        fail_reason:  the string reason from extract_java_code / post_process_java
        bad_response: the raw LLM response that was rejected
        method:       method metadata dict
        plan:         the accepted plan text from Step 1
    """
    class_name  = method.get('class_name', '')
    method_name = method.get('method_name', '')
    test_class_name = f"{class_name}_{method_name}_Test"
    package = '.'.join(method.get('full_name', '').split('.')[:-2])

    raw_instruction = _RECOVERY_INSTRUCTIONS.get(
        fail_reason,
        f"Your response was rejected ({fail_reason}). Regenerate the test class correctly.",
    )
    instruction = raw_instruction.format(class_name=class_name)

    prompt = f"""Your previously generated test was rejected for the following reason:
{instruction}

Re-implement the test class by following the accepted plan below exactly.
Do NOT re-plan — implement what the plan already specifies.

=== ACCEPTED TEST PLAN ===
{plan or '(no plan available)'}

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}

=== YOUR PREVIOUS (REJECTED) RESPONSE ===
{bad_response or '(no response)'}

=== STRICT RULES ===
1. Class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. The class under test `{class_name}` MUST appear in the code body as a type, \
constructor call, or static reference — not just in an import.
4. Every test method MUST be annotated with @Test.
5. Write meaningful assertions (assertEquals, assertThrows, assertTrue/assertFalse). \
NEVER write assertTrue(true) or leave a test body empty.
6. The file must contain exactly one TOP-LEVEL type: {test_class_name}. \
Helper types may be `static` nested classes INSIDE {test_class_name} but must not \
share a name with any production class. Do NOT add extra top-level classes.
7. Do NOT use Mockito or any mocking framework.
8. ONLY add imports listed in PLANNED IMPORTS in the plan above.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code starting with the package declaration. \
No explanations, no markdown fences.

Generate the corrected test class now:
"""
    return prompt