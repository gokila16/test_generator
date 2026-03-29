import re


def _format_imports(method):
    imports = method.get('source_file_imports', [])
    if not imports:
        return ""
    lines = ["// SOURCE FILE IMPORTS (use these when adding imports — do NOT add imports for unlisted classes):"]
    for imp in imports:
        lines.append(f"//   {imp}")
    return "\n".join(lines)


def _format_dependencies_for_retry(method, generated_test_code):
    """
    Returns dependency signatures only for classes that were actually
    referenced in the generated test code. Javadoc is excluded intentionally
    — at retry time only the exact type signatures matter.
    """
    deps = method.get('dependency_signatures', [])
    if not deps:
        return ""

    # Group by class
    by_class = {}
    for d in deps:
        by_class.setdefault(d['class_name'], []).append(d)

    # Only include classes that appear in the generated test
    referenced_classes = {
        cls for cls in by_class if cls in generated_test_code
    }

    if not referenced_classes:
        return ""

    lines = ["// DEPENDENCY SIGNATURES for classes used in your generated test:"]
    lines.append("// Use ONLY these exact signatures — do NOT invent method names, parameter types, or constructors.")
    for class_name in referenced_classes:
        lines.append(f"//   Class: {class_name}")
        for d in by_class[class_name]:
            lines.append(f"//     [{d['kind']}] {d['signature']}")

    return "\n".join(lines)


def build_base_prompt(method):
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    import_section = _format_imports(method)

    prompt = f"""You are an expert Java software engineer specializing in writing high-quality JUnit 5 unit tests.

Your task is to write a complete, compilable JUnit 5 test class for the Java method provided below.

=== OUTPUT FORMAT ===
Output ONLY the raw Java source code. No explanations, no markdown fences, no comments outside the code.
The output must start with the package declaration.

=== STRICT CONSTRAINTS ===
1. Test class name MUST be exactly: {test_class_name}
2. Package MUST be exactly: {package}
3. For every non-java.lang class you reference in the test, you MUST add an explicit import statement. Copy the exact import line from SOURCE FILE IMPORTS. If a class is not in that list and is not a JUnit 5 / Java SE class, do NOT use it.
4. Do NOT use internal implementation classes (e.g. classes whose names start with RandomAccess*, *Buffered*, *Parser, *Reader) in your test — only use the public API types needed to call the method and verify its return value.
5. NEVER pass bare null to any overloaded method. Always cast null to the exact parameter type (e.g. `(File) null`, `(InputStream) null`, `(String) null`).
6. Do NOT access private fields or methods of any class.
7. Do NOT test private implementation details — test observable behavior through the public API only.
8. If you receive an object as the return value of a dependency method 
and its class is not listed in DEPENDENCY SIGNATURES, do not call 
any methods on it. Only assert that it is not null.

=== TEST QUALITY REQUIREMENTS ===
- Write 2-3 focused test methods covering: the normal/happy-path, at least one edge case (null, empty, boundary), and expected exceptions if the method declares any throws.
- Annotate each test with @Test and give it a descriptive camelCase name that states what is being tested.
- Include at least one specific assertion per test (use assertEquals, assertThrows, assertNotNull with a follow-up check, etc.).
- Use @BeforeEach for shared setup if the method requires object instantiation.
- If the method throws a checked exception, declare `throws <ExceptionType>` on the test method signature rather than wrapping in try-catch, unless you are specifically testing for that exception with assertThrows.
- Do NOT use Mockito or any mocking framework unless it is in the source imports.

=== METHOD UNDER TEST ===
// Signature:
{method.get('signature', '')}
"""

    if method.get('javadoc'):
        prompt += f"\n// Javadoc:\n{method['javadoc']}\n"

    prompt += f"""
// Implementation:
{method.get('body', '')}

"""

    if import_section:
        prompt += import_section + "\n\n"

    prompt += "\nGenerate the test class now:"
    return prompt


def build_retry_prompt(error_message, failing_test, method):
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    import_section = _format_imports(method)
    dep_section = _format_dependencies_for_retry(method, failing_test)

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
3. Fix ONLY what the error points to — do NOT rewrite parts that were correct.
4. Use ONLY the method signatures listed under DEPENDENCY SIGNATURES — do NOT invent names, parameter types, or constructors.
5. For every non-java.lang class you reference, add an explicit import. Copy the exact import line from SOURCE FILE IMPORTS. If a class is not listed there and is not a JUnit 5 / Java SE class, remove it from the test.
6. Do NOT use internal implementation classes — only use public API types needed to call the method and verify its return value.
7. NEVER pass bare null to any overloaded method. Always cast: `(File) null`, `(InputStream) null`, `(String) null`.
8. Do NOT call any method not listed in DEPENDENCY SIGNATURES, even if you can see it called inside the method implementation body.
9. Do NOT access private fields or methods of any class.
10. Do NOT guess constructors — only instantiate a class if its constructor is explicitly listed in DEPENDENCY SIGNATURES.
11. If a meaningful test cannot be written, produce the simplest test that compiles and passes.
12.If you receive an object as the return value of a dependency method 
and its class is not listed in DEPENDENCY SIGNATURES, do not call 
any methods on it. Only assert that it is not null.

=== ERROR DIAGNOSIS GUIDE ===
- "reference to X is ambiguous" → you passed uncast null to an overloaded method. Cast it: (ExpectedType) null.
- "cannot find symbol: method X" → that method does not exist. Check DEPENDENCY SIGNATURES and use only what is listed.
- "cannot find symbol: class X" → missing import or class does not exist. Check SOURCE FILE IMPORTS.
- "cannot be instantiated" → the class is abstract. Do not instantiate it directly.
- "no suitable constructor found" → you guessed the constructor. Check DEPENDENCY SIGNATURES for the correct one.
- "does not override or implement" → your method signature does not match the supertype. Check the exact signature in DEPENDENCY SIGNATURES.
- "has private access" → you accessed a private member. Only use public API visible in DEPENDENCY SIGNATURES.

=== OUTPUT FORMAT ===
Output ONLY the corrected raw Java source code, starting with the package declaration. No explanations.

"""

    if import_section:
        prompt += import_section + "\n\n"

    if dep_section:
        prompt += dep_section + "\n"

    prompt += "\nGenerate the corrected test class now:"
    return prompt