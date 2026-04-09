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
    lines = ["DEPENDENCY SIGNATURES (only these methods may be called on dependency objects):"]
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
        "guaranteed to be valid and parseable by PDFBox:",
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
        "The following snippets show how this method is actually called in the PDFBox codebase.",
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


def build_planning_prompt(method, dep_chain=None, caller_snippets=None):
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

    if resource_block:
        prompt += resource_block + "\n\n"

    construction_section = _format_construction_section(dep_chain)
    if construction_section:
        prompt += construction_section + "\n\n"

    caller_section = _format_caller_snippets(caller_snippets)
    if caller_section:
        prompt += caller_section + "\n\n"

    prompt += f"""=== HARD RULES ===
1. You MUST NOT reference any method that is not listed in DEPENDENCY SIGNATURES. Every method call you plan must appear verbatim in that list under the correct class.
2. Write each planned method call as ClassName.methodName(ParamType1, ParamType2) so it is unambiguous.
3. For PLANNED IMPORTS, you MUST only list imports that appear in SOURCE FILE IMPORTS, plus JUnit 5 (org.junit.jupiter.api.*) and Java SE (java.*) classes. Do NOT invent or add any other import. Always include java.net.URISyntaxException when using getResource(). Never include java.net.URL — it is not needed.
4. Do NOT plan to use Mockito or any mocking framework.
5. Do NOT plan to access private fields or methods.
6. NEVER plan to pass bare null to an overloaded method — always plan to cast it (e.g. (File) null).
7. Never plan chained method calls. Every method return value must be assigned to a named variable before calling methods on it.
8. Never use File.createTempFile() for happy path tests. Always use a real resource file from AVAILABLE TEST RESOURCE FILES above for valid-input tests.
9. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource("FILENAME").toURI() with a filename from AVAILABLE TEST RESOURCE FILES.
10. Never plan assertThrows(IOException.class, () -> method(null)). Passing null to a method that dereferences it throws NullPointerException, not a checked exception. Plan null input tests with assertThrows(NullPointerException.class, ...) or avoid null input tests entirely.
11. You MUST use the exact construction statements provided in HOW TO CONSTRUCT EACH INPUT. Do not invent, simplify, or replace them. If a parameter has no construction provided, write a TODO comment for it and do not guess.

=== REQUIRED OUTPUT FORMAT ===
Output exactly the following structure. Fill in each section — do not skip any.

PLANNED IMPORTS:
- <exact import statement>
- <exact import statement>
...

TEST METHODS:
1. <camelCase test method name>
   Scenario: <one sentence describing what this test verifies>
   Setup: <objects to instantiate and how, or "none">
   Method calls: <ClassName.methodName(ParamType), ...>
   Assertions: <exact assertion type and what is being checked>

2. <camelCase test method name>
   Scenario: ...
   Setup: ...
   Method calls: ...
   Assertions: ...

(2-3 test methods total covering: happy path, edge case, and expected exception if the method declares throws)

Output the plan now:"""

    return prompt


def build_generation_from_plan_prompt(method, plan):
    """
    Step 2 of 2: asks the LLM to generate the test class by implementing the plan exactly.
    Imports are restricted to exactly what was listed in the plan.
    """
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])

    prompt = f"""You are an expert Java software engineer. Implement the following test plan as a complete, compilable JUnit 5 test class.

=== TEST PLAN ===
{plan}

=== METHOD UNDER TEST ===
Signature: {method.get('signature', '')}

Implementation:
{method.get('body', '')}

=== HARD RULES ===
1. Test class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. Implement EXACTLY the test methods listed in the plan — same names, same method calls, same assertions. Do NOT add, remove, or rename any test method.
4. ONLY add import statements that are listed verbatim in the PLANNED IMPORTS section of the plan. Do NOT add any import that is not in that list.
5. Do NOT use any class, method, or constructor that is not in the plan.
6. NEVER pass bare null to an overloaded method — cast it as specified in the plan (e.g. (File) null).
7. Do NOT access private fields or methods.
8. Do NOT use Mockito or any mocking framework.
9. If the method throws a checked exception, declare `throws <ExceptionType>` on the test method rather than wrapping in try-catch, unless the plan uses assertThrows.
10. Never chain method calls. Always assign return values to named variables before calling methods on them.
11. Never construct file content programmatically for happy path tests. Use the resource files specified in the plan.
12. Never hardcode placeholder file paths. Use getClass().getClassLoader().getResource() as specified in the plan.
13. Never pass a raw InputStream or FileInputStream where a RandomAccessRead is required. Wrap it using RandomAccessReadBuffer or RandomAccessReadBufferedFile as appropriate.
14. Never write assertThrows(IOException.class, () -> method(null)). Passing null to a method that dereferences it throws NullPointerException, not a checked exception. Test null inputs with assertThrows(NullPointerException.class, ...) or avoid null input tests entirely.
15. Use the exact construction code from the plan setup section verbatim. Do not rewrite, simplify, or replace object construction code. The setup code was pre-verified — copying it exactly is required.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code. No explanations, no markdown fences.
The output must start with the package declaration.

Generate the test class now:"""

    return prompt


def build_retry_prompt(error_message, failing_test, method):
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
4. You MUST ONLY call methods listed in ALLOWED DEPENDENCY SIGNATURES below. Do NOT call any method not in that list.
5. For every non-java.lang class you reference, add an explicit import. Copy the exact import line from SOURCE FILE IMPORTS. If a class is not listed there and is not a JUnit 5 / Java SE class, remove it from the test.
6. Do NOT use internal implementation classes — only use public API types needed to call the method and verify its return value.
7. NEVER pass bare null to any overloaded method. Always cast: `(File) null`, `(InputStream) null`, `(String) null`.
8. Do NOT access private fields or methods of any class.
9. Never chain method calls. Always assign return values to named variables before calling methods on them.
10. If a meaningful test cannot be written, produce the simplest test that compiles and passes.

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