import re
import config
from src.behavioral_analyzer import extract_behavioral_constraints, extract_branches
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
        is_final      = entry.get('is_final', False)
        constructors  = entry.get('constructors', [])
        factory_meths = entry.get('factory_methods', [])
        pub_methods   = entry.get('public_methods', [])
        subclasses    = entry.get('concrete_subclasses', [])

        if is_interface:
            kind = "interface"
        elif is_abstract:
            kind = "abstract class"
        elif is_final:
            kind = "final class"
        else:
            kind = "class"
        lines.append(f"  {class_name} [{kind}]")
        if is_final:
            lines.append(
                f"    !! FINAL — cannot be subclassed, extended, or anonymously instantiated "
                f"with {{ }}. Do not write new {class_name}() {{ ... }}."
            )

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


def _infer_semantic_annotation(ptype: str, construction: str, strategy: str) -> str:
    """
    Returns a brief plain-English comment explaining what a constructed value
    represents.  Used to anchor oracle derivation without asking the LLM to
    trace through multiple layers of construction logic.
    """
    c = construction.lower()
    t = ptype

    if strategy == 'resource':
        return f"// a valid {t} backed by a real test resource file"
    if 'load' in c or 'parse' in c or 'open' in c:
        return f"// a fully loaded/parsed {t} instance"
    if re.search(r'new\s+' + re.escape(t) + r'\s*\(\s*\)', c):
        return f"// a default-constructed (empty) {t} instance"
    if 'new ' + t.lower() in c:
        return f"// a newly constructed {t} instance"
    if 'getresourceasstream' in c or 'getresource' in c:
        return f"// an {t} backed by a real test resource file"
    return f"// a {t} instance"


def _format_construction_section(dep_chain):
    """
    Formats the HOW TO CONSTRUCT EACH INPUT section from a dep_chain entry.
    Each entry is annotated with a plain-English comment describing what the
    constructed value represents, so the LLM can derive oracle values without
    re-tracing the construction logic.
    Skips params with unresolvable strategies and notes them as TODOs.
    Returns empty string if dep_chain is None or has no params/receiver.
    """
    if not dep_chain:
        return ""

    lines = [
        "## HOW TO CONSTRUCT EACH INPUT",
        "Use these exact statements verbatim. Do not invent alternatives.",
        "",
    ]

    receiver = dep_chain.get('receiver')
    if receiver:
        if receiver.get('strategy') in _SKIP_STRATEGIES:
            lines.append("Receiver (instance of the class under test):")
            lines.append("  // TODO: receiver could not be resolved — do not guess construction code.")
        else:
            construction = receiver.get('construction', '')
            rtype = dep_chain.get('class_name', '')
            annotation = _infer_semantic_annotation(rtype, construction, receiver.get('strategy', ''))
            lines.append(f"Receiver (instance of the class under test):  {annotation}")
            lines.append(f"  {construction}")
        lines.append("")

    params = dep_chain.get('params') or []
    for i, p in enumerate(params, 1):
        strategy = p.get('strategy', '')
        if strategy == 'skip':
            continue  # output-type param — exclude entirely
        ptype = p.get('type', '')
        pname = p.get('name', '')
        lines.append(f"Parameter {i}: {ptype} {pname}")
        if strategy in _SKIP_STRATEGIES:
            lines.append(f"  // TODO: parameter could not be resolved — do not invent construction code for it.")
        else:
            construction = p.get('construction', '')
            annotation = _infer_semantic_annotation(ptype, construction, strategy)
            lines.append(f"  {annotation}")
            lines.append(f"  {construction}")
        lines.append("")

    if len(lines) <= 3:
        return ""  # nothing useful was added
    return "\n".join(lines)


def _format_pre_computed_facts(method, testable_slices=None):
    """
    Combines statically-extracted behavioral facts into a single section that
    the LLM reads as pre-verified ground truth — not as something to derive.

    Covers:
      - Branch structure (if-condition → taken/not-taken outcomes)
      - Literal return values per conditional path
      - Throw conditions with their guard predicates
      - Ternary return paths from method slicing

    Presenting these as facts rather than asking the LLM to trace code is the
    primary defence against oracle hallucination.
    """
    body = method.get('body', '')
    if not body:
        return ""

    behavioral = extract_behavioral_constraints(body)
    branches   = extract_branches(body)

    lines = [
        "## PRE-COMPUTED BEHAVIORAL FACTS",
        "The following are read directly from the source — treat them as ground truth.",
        "Each fact corresponds to a specific execution path. Use these exact values as the",
        "expected output in your assertions — do NOT re-derive or guess different values.",
        "Your job: pick the INPUT that forces each path, then assert the outcome listed here.",
        "",
    ]

    # Branch structure
    if branches:
        lines.append("Branch map (statically extracted):")
        for n, b in enumerate(branches, 1):
            taken     = b['taken']
            not_taken = b['not_taken']
            if not_taken:
                lines.append(f"  Branch {n}: if ({b['condition']}) -> {taken} | else -> {not_taken}")
            else:
                lines.append(f"  Branch {n}: if ({b['condition']}) -> {taken}")
        lines.append("")

    # Throw contracts
    throw_lines = []
    for t in behavioral.get('throws', []):
        if t['condition'] is not None:
            throw_lines.append(
                f"  throws {t['exception']} when ({t['condition']})"
            )
        else:
            throw_lines.append(f"  throws {t['exception']} (unconditionally or guard not detected)")
    if throw_lines:
        lines.append("Throw contracts:")
        lines.extend(throw_lines)
        lines.append("")

    # Literal return values
    return_lines = []
    for r in behavioral.get('returns', []):
        return_lines.append(f"  returns {r['value']}  (on the path: `{r['context']}`)")
    if return_lines:
        lines.append("Literal return values:")
        lines.extend(return_lines)
        lines.append("")

    # Ternary paths from method slicing
    ternary_slices = [
        s for s in (testable_slices or [])
        if s.get('slice_type') == 'RETURN_SLICE'
        and ('is true' in s.get('entry_condition', '')
             or 'is false' in s.get('entry_condition', ''))
    ]
    if ternary_slices:
        lines.append("Ternary return paths (from method slicing — one test required per line):")
        for s in ternary_slices:
            lines.append(f"  When {s['entry_condition']}: {s['expected_observable']}")
        lines.append("")

    if len(lines) <= 4:
        return ""  # nothing useful extracted
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

    Section order is intentional — grounding data (real usage, concrete
    construction, pre-computed facts) comes before any task instructions so the
    model is anchored to real values before it starts reasoning.
    """
    class_name = method['class_name']

    # ---- Build every context section up-front ----
    import_section     = _format_imports(method)
    dep_section        = _format_dependency_signatures(method)
    resource_block     = _format_resource_block(method)
    inventory_section  = _format_class_inventory_section(method, class_inventory or {})
    construction_section = _format_construction_section(dep_chain)
    abstract_hint      = _format_abstract_receiver_hint(method, dep_chain, class_inventory or {})
    caller_section     = _format_caller_snippets(caller_snippets)
    pre_computed       = _format_pre_computed_facts(method, testable_slices)

    # ---- Persona + method ----
    prompt = f"""You are a senior SDET. You read the implementation to understand EVERY path
the method can take — every branch, guard, early return, exception, and edge case.
Then you design tests that EXERCISE each path from the outside: you craft an input
that forces the method down a specific path, and you assert the known outcome for
that path.

=== HOW YOU THINK ===
1. Read the implementation to build a complete map of all paths:
   - Which conditions lead to which return values or exceptions?
   - What inputs trigger each guard clause or early return?
   - What are the boundary conditions where behavior changes?
2. For EACH path, determine:
   - What concrete input forces the method down THIS path?
   - What is the KNOWN outcome (return value, exception, state change) for this path?
   - Use PRE-COMPUTED BEHAVIORAL FACTS as ground truth for these outcomes.
3. Write a test for each path: construct the input, call the method ONCE, assert
   the known outcome. The expected value is a HARDCODED LITERAL in the test — you
   already know what it should be from step 2.
4. The test code itself must NEVER contain the same branching logic as the method.
   You are not re-running the method's logic — you are verifying its result for a
   specific input where you already know the answer.

=== THE KEY DISTINCTION ===
KNOWING all paths (good) vs. COPYING the logic (bad):
- GOOD: "I see that if (x == null) the method throws IllegalArgumentException.
         So my test passes null and does assertThrows(IllegalArgumentException.class, ...)."
- BAD:  "My test checks if (x == null) and then expects an exception, else expects a value."
         This mirrors the production code — if the production code is wrong, the test is
         also wrong in the same way. It catches nothing.

=== WHAT MAKES A BAD TEST (DO NOT WRITE THESE) ===
- Re-implementing the method's if/else/switch in the test to compute expected values.
- assertNotNull(x) as the only assertion — passes even if x is completely wrong.
- assertTrue(true), empty test body, or no assertion at all.
- Importing classes that are never referenced in any test method.
- "Smoke tests" that just call the method without verifying the return value.

Do NOT write any Java code. Output only the structured plan described at the end.

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}
"""

    if method.get('javadoc'):
        prompt += f"Javadoc:\n{method['javadoc']}\n"

    prompt += f"""
Implementation (read to understand all paths — do NOT copy this logic into your tests):
{method.get('body', '')}

"""

    # ---- 1. Real usage examples — FIRST (best grounding) ----
    if caller_section:
        prompt += caller_section + "\n\n"

    # ---- 2. Concrete construction — SECOND ----
    if construction_section:
        prompt += construction_section + "\n\n"

    if abstract_hint:
        prompt += abstract_hint + "\n\n"

    # ---- 3. What methods/classes exist ----
    if dep_section:
        prompt += dep_section + "\n\n"

    if inventory_section:
        prompt += inventory_section + "\n\n"

    if resource_block:
        prompt += resource_block + "\n\n"

    if import_section:
        prompt += import_section + "\n\n"

    # ---- 4. Pre-computed facts — read these, don't re-derive ----
    if pre_computed:
        prompt += pre_computed + "\n\n"

    # ---- 5. Task — focused, compact ----
    prompt += f"""=== YOUR TASK ===
Answer the three questions below, then write the test plan.

QUESTION 1 — WHAT DOES THIS METHOD DO?
State the method's purpose in one sentence: what it computes, validates, or transforms,
and what a caller expects to get back.

QUESTION 2 — PATH-BY-PATH TEST TABLE
List EVERY distinct execution path through the method (every if/else branch, every
guard clause, every catch block, every early return, every loop-skip scenario).
For EACH path, fill in this line:
  Path N: <input that forces this path> → expected: <exact return value, exception type, or state change>
Rules:
  - Use PRE-COMPUTED BEHAVIORAL FACTS as ground truth for return values and exceptions.
  - The expected value must be a CONCRETE LITERAL or specific exception type.
  - If the method returns an object, state which GETTER/FIELD to check and its expected value
    (e.g., result.getSize() == 0, result.getName().equals("default")).
    "returns an object" or "returns non-null" is NOT a testable outcome.
  - Every path needs an input that reaches it. Use HOW TO CONSTRUCT and REAL USAGE EXAMPLES
    to find realistic inputs. If no input can reach a path, mark it "unreachable" and skip.

QUESTION 3 — REGRESSION SCENARIOS
For each parameter, name the one input most likely to expose a bug if a developer
carelessly modifies this method (null, empty, negative, max-value, partially-initialised).
State what SHOULD happen: throw (which exception?), return a default, or corrupt state.
Only propose tests for inputs the method actually receives — no invented params.

Then write the test plan:

=== CRITICAL RULES ===
1. ONLY call methods listed in DEPENDENCY SIGNATURES or AVAILABLE PROJECT CLASSES.
   For any other class — do not call any instance or static method on it at all.
2. Use the EXACT construction statements from HOW TO CONSTRUCT EACH INPUT verbatim.
   Do not simplify, replace, or invent alternatives. If no construction is provided, write
   a TODO comment and omit that test.
3. Every assertion must use a HARDCODED expected value from PRE-COMPUTED BEHAVIORAL FACTS
   or your QUESTION 2 table. The expected value goes directly into the assertion — you must
   NOT compute it by re-running the production logic in the test.
   Acceptable: assertEquals("expected_string", result)
   Acceptable: assertThrows(IOException.class, () -> method(null))
   Acceptable: assertTrue(result.isEmpty())
   FORBIDDEN: assertNotNull(x) as sole assertion, assertTrue(true), empty test body.
4. Do NOT use Mockito or any mocking framework. Do NOT create anonymous subclasses
   (new ClassName() {{ ... }} syntax is forbidden for all classes).
5. The class under test is `{class_name}`. Never substitute a different class as receiver.
   If it is abstract, use a listed concrete subclass but declare the variable as `{class_name}`.

=== REQUIRED OUTPUT FORMAT ===
Output exactly this structure. Do not skip any section.

METHOD SEMANTICS:
Purpose: <one sentence>
Path-by-path oracle (from QUESTION 2 — one line per path, these are the GROUND TRUTH):
  - Path N: <input description> → <exact expected outcome>
Assertion for each test (EVERY test must have one — no exceptions):
  - Test <name>: assertEquals(<hardcoded expected>, result)
  - Test <name>: assertThrows(<ExceptionType>.class, ...)
  - Test <name>: result = method(...); assertEquals(<expected>, result.getField())
  FORBIDDEN: assertNotNull alone, assertTrue(true), empty body, no assertion

PATH ANALYSIS:
Path 1: <condition> → <outcome when taken> | <outcome when not taken>
Path 2: ...
(list every path through the method; mark which come from PRE-COMPUTED BEHAVIORAL FACTS)

PLANNED IMPORTS (only what YOUR test methods actually need — not everything from SOURCE FILE IMPORTS):
- <exact import statement from SOURCE FILE IMPORTS or JUnit 5 / Java SE only>
...

TEST METHODS (one test per path — every path must be tested):
1. <camelCaseTestName>
   Tests path: <Path N — which execution path this exercises>
   Regression caught: <what bug this catches — e.g. "fails if null guard is removed">
   Setup: <verbatim construction from HOW TO CONSTRUCT, or "none">
   Input: <what specific value/state forces the method down this path>
   Method call: <exact call — receiver.methodName(args)>
   Expected output: <hardcoded value from Path-by-path oracle>
   Assertion: <assertEquals/assertThrows/assertTrue with specific expected value — NEVER assertNotNull alone>

2. <camelCaseTestName>
   ...

(one test per path from QUESTION 2; additional tests for regressions from QUESTION 3)

PATH COVERAGE SUMMARY:
- Path 1: covered by test <name>
- Path 2: covered by test <name>
(every path must appear here — if a path has no test, explain why)

Output the plan now:"""

    return prompt


def build_generation_from_plan_prompt(method, plan, dep_chain=None):
    """
    Step 2 of 2: asks the LLM to generate the test class by implementing the plan exactly.
    Imports are restricted to exactly what was listed in the plan.
    dep_chain is passed through so construction statements are available as a
    direct reference — the model does not have to re-read them from the plan text.
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    resource_block = _format_resource_block(method)
    construction_section = _format_construction_section(dep_chain)

    prompt = f"""You are a senior SDET implementing a test plan as JUnit 5 Java code.
Each test targets a specific execution path through the method. The plan already
specifies the input, the path it exercises, and the expected output. Your job is to
translate each test into compilable code — not to re-derive or second-guess the plan.

=== YOUR WORKFLOW FOR EACH @Test METHOD ===
1. Find the test in the plan's TEST METHODS section.
2. Read its "Input" and "Setup" fields — copy the construction code from
   HOW TO CONSTRUCT (or the plan's Setup field) VERBATIM.
3. Write the method call exactly as specified in "Method call".
4. Read the "Expected output" and "Assertion" fields.
5. Write that assertion with the HARDCODED expected value from the plan.
   Do NOT look at the production implementation to compute the expected value.

=== QUALITY GATE — EVERY @Test METHOD MUST PASS THIS ===
- Contains at least one assertEquals, assertThrows, or assertTrue/assertFalse
  with a SPECIFIC hardcoded expected value.
- The expected value is a LITERAL in the test code, NOT computed by re-running
  the method's branching logic. Your test must NOT contain the same if/else/switch
  structure as the method under test.
- assertNotNull(x) alone is never enough. Always follow with a value/state assertion.
- If you cannot write a meaningful assertion, OMIT that test method entirely.
  An always-passing test is worse than no test.
- Only import classes you actually reference in a test method body.

=== TEST PLAN ===
{plan}

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}

"""

    # Re-include construction section so the model has it as a direct reference
    # without having to extract it from the plan text.
    if construction_section:
        prompt += construction_section + "\n\n"

    if resource_block:
        prompt += resource_block + "\n\n"

    prompt += f"""=== RULES ===
1. Class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. Implement EXACTLY the test methods in the plan — same names, same method calls, same assertions.
4. ONLY add imports listed in PLANNED IMPORTS. Remove any import you don't use in a test body.
5. Use the exact construction code from the plan Setup field (or HOW TO CONSTRUCT above) verbatim.
6. Never chain method calls. Assign every return value to a named variable first.
7. NEVER pass bare null to an overloaded method — cast it (e.g. (File) null).
8. Do NOT use Mockito or any mocking framework.
9. Do NOT create anonymous subclasses (new ClassName() {{ ... }} syntax is forbidden).
10. Every assertion must use the HARDCODED expected value from the plan's "Expected output" field.
    Do NOT re-derive expected values by re-implementing the production code's branching logic.
11. assertNotNull alone is never sufficient — always follow it with assertEquals or similar.
12. NEVER write assertTrue(true) or leave a @Test body empty. Omit the test entirely instead.
13. One top-level type only: `{test_class_name}`. Helper types must be static nested classes
    inside it and must not share a name with any production class.
14. The class under test is `{method['class_name']}`. It MUST appear as a type, constructor call,
    or static reference in the code body — not just in an import.
15. If the method throws a checked exception, declare `throws <ExceptionType>` on the test method.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code starting with the package declaration. No explanations, no markdown fences.

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
8a. NEVER create anonymous subclasses or anonymous implementations using new ClassName() {{ ... }} syntax. This is forbidden for ALL classes. If you cannot construct an object through its public constructor or factory method, write a TODO comment and omit that test entirely.
9. Never chain method calls. Always assign return values to named variables before calling methods on them.
10. If a specific assertion cannot be written due to access constraints, omit that test method entirely. NEVER write assertTrue(true) or leave a test body empty — a test that always passes regardless of production code is worse than no test.
11. assertNotNull alone is NEVER sufficient — always follow it with assertEquals, assertTrue/assertFalse, or another assertion that checks a specific value or state.
12. NEVER reproduce the production method's logic in the test. Do not re-implement the same if/else/switch/loop structure. The test must check against a KNOWN expected value, not re-derive it.
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
8a. NEVER create anonymous subclasses or anonymous implementations using new ClassName() {{ ... }} syntax. This is forbidden for ALL classes. If you cannot construct an object through its public constructor or factory method, write a TODO comment and omit that test entirely.
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
        "All your test methods have weak or missing assertions. "
        "assertNotNull alone is NOT sufficient — it does not verify correctness. "
        "Every @Test method MUST contain at least one assertEquals(expected, actual), "
        "assertThrows(Exception.class, ...), or assertTrue/assertFalse with a specific "
        "condition derived from the method's documented behavior. "
        "Do NOT reproduce the production method's logic to compute expected values. "
        "Use the exact values from the test plan's Assertion derivation section. "
        "If a test scenario cannot produce a meaningful assertion, omit that @Test entirely."
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