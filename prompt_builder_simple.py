"""
prompt_builder_simple.py

Three prompts for the simplified pipeline used on the `simple` bucket:
  - build_simple_plan_prompt(method)              → enumerates test cases (no code)
  - build_simple_generation_prompt(method, plan)  → writes Java tests under a strict allowlist
  - build_simple_retry_prompt(method, code, error)→ fixes errors, allowlist still applies

Anti-hallucination strategy:
  1. The plan prompt sees only signature/body/javadoc — no API surface to hallucinate.
  2. The generation prompt receives an explicit "ALLOWED METHOD CALLS" list built
     from the method's Understand-derived `dependency_signatures` plus the SUT
     class's own public methods/constructors from class_inventory.json. The
     prompt forbids any other method or class.
  3. For overloaded methods the exact signature being tested is highlighted; the
     generated test class is named with the overload index; sibling overloads
     are explicitly listed with a "do not test these" callout so the LLM doesn't
     conflate them.
  4. Imports are constrained to the SUT's own `source_file_imports` plus JUnit
     Jupiter — preventing fabricated import paths.
  5. The same package as the SUT is forbidden as an import (Checkstyle's
     RedundantImport rule fails the build otherwise).

Inputs are dict-shaped method records from extracted_metadata_simple.json
plus a class_inventory dict. The caller (pipeline_step3_simple) injects two
extra fields into the method dict before calling these:
    method["_test_class_name"] — e.g. PDColor_PDColor_0_Test
    method["_package"]         — e.g. org.apache.pdfbox.pdmodel.graphics.color
"""

from __future__ import annotations


_JUNIT_ASSERTIONS = (
    "assertEquals", "assertNotEquals", "assertNull", "assertNotNull",
    "assertTrue", "assertFalse", "assertThrows", "assertDoesNotThrow",
    "assertSame", "assertNotSame", "assertArrayEquals", "assertIterableEquals",
    "assertAll", "fail",
)


def _abstractness_marker(class_inventory: dict | None, full_name: str) -> str:
    """Return ` [INTERFACE]`, ` [ABSTRACT]`, or '' for the class containing
    the entity at *full_name*. Looked up via class_inventory by FQN."""
    if not class_inventory or not full_name:
        return ""
    cls_fqn = full_name.rsplit(".", 1)[0]
    entry = class_inventory.get(cls_fqn)
    if not entry:
        return ""
    if entry.get("is_interface"):
        return " [INTERFACE — cannot be instantiated]"
    if entry.get("is_abstract"):
        return " [ABSTRACT — cannot be instantiated]"
    return ""


def _format_dep_lines(deps: list, class_inventory: dict | None = None) -> str:
    """Render dependency_signatures as a strict allowlist, flagging callees
    whose containing class is abstract or an interface."""
    if not deps:
        return "  (none — only JUnit Jupiter assertions and java.lang.* are available)"
    lines = []
    for d in deps:
        cn = d.get("class_name", "")
        sg = d.get("signature", "").replace("\n", " ").strip()
        kind = d.get("kind", "")
        marker = _abstractness_marker(class_inventory, d.get("full_name", ""))
        lines.append(f"  - {cn}: {sg}    [{kind}]{marker}")
    return "\n".join(lines)


def _format_sut_api(class_inventory: dict | None, full_name: str, class_name: str) -> str:
    """Render the SUT class's own public methods + constructors as part of the allowlist.

    Looks up the class's FQN by stripping the method name from full_name. Tags
    the class header if abstract / interface so the LLM doesn't try to do
    `new ClassName()`.
    """
    if not class_inventory or not full_name:
        return "  (class_inventory unavailable)"
    cls_fqn = full_name.rsplit(".", 1)[0]
    entry = class_inventory.get(cls_fqn)
    if not entry:
        return "  (class not found in inventory)"
    sut_marker = ""
    if entry.get("is_interface"):
        sut_marker = "  *** SUT IS AN INTERFACE — cannot be instantiated; pass null with cast or use a concrete subclass ***"
    elif entry.get("is_abstract"):
        sut_marker = "  *** SUT IS ABSTRACT — cannot be instantiated; pass null with cast or use a concrete subclass ***"
    out = []
    if sut_marker:
        out.append(sut_marker)
    for ctor in entry.get("constructors", []):
        if ctor.get("visibility") == "public":
            params = ", ".join(ctor.get("params", []))
            out.append(f"  - {class_name}({params})    [Public Constructor]")
    for m in entry.get("public_methods", []):
        out.append(f"  - {class_name}.{m}    [Public Method]")
    if not out:
        return "  (no public API)"
    return "\n".join(out)


def _format_sibling_overloads(class_inventory: dict | None, full_name: str,
                              method_name: str, current_signature: str) -> str:
    """For overload disambiguation: list other overloads of the same method name."""
    if not class_inventory or not full_name:
        return ""
    cls_fqn = full_name.rsplit(".", 1)[0]
    entry = class_inventory.get(cls_fqn)
    if not entry:
        return ""
    cur_sig = (current_signature or "").replace(" ", "")
    overloads = []
    for m in entry.get("public_methods", []):
        m_clean = m.replace(" ", "")
        if (m_clean.startswith(method_name + "(") or f" {method_name}(" in (" " + m_clean)) \
                and m_clean != cur_sig and method_name in m:
            overloads.append(f"  - {m}")
    if not overloads:
        return ""
    return (
        "\n=== OTHER OVERLOADS OF THIS METHOD NAME (DO NOT TEST THESE) ===\n"
        + "\n".join(overloads)
    )


def _format_imports(imports: list) -> str:
    if not imports:
        return "  (none)"
    return "\n".join(f"  - {i}" for i in imports)


# ---------------------------------------------------------------------------
# Prompt 1: planning
# ---------------------------------------------------------------------------

def build_simple_plan_prompt(method: dict) -> str:
    """Enumerate test cases without writing Java code.

    Deliberately scoped: gives the LLM only the method itself (signature, body,
    javadoc) so it cannot invent API surface in the plan.
    """
    sig = method.get("signature", "")
    body = method.get("body", "")
    javadoc = method.get("javadoc") or "(no javadoc)"
    full_name = method.get("full_name", "")

    return f"""You are planning JUnit 5 tests for ONE Java method. Do NOT write any Java code in your output — only enumerate test cases.

=== METHOD ===
Full name: {full_name}
Signature: {sig}

Javadoc:
{javadoc}

Body:
{body}

=== TASK ===
List every distinct test case that must be written for THIS exact signature.

Required coverage:
  1. Each branch in the body (every if-else arm, switch case, ternary).
  2. Each distinct return value class (different objects, true/false, null vs. non-null, edge numerics like 0 / -1 / Integer.MAX_VALUE).
  3. Null and empty inputs for every reference-type parameter where they're plausible.
  4. Every documented exception (`@throws`) in the javadoc.
  5. For getters: at least one test with a non-default state and one with the default/uninitialized state.
  6. For setters: verify the corresponding getter reflects the set value.
  7. For constructors: verify each field-state assertion that the body implies (e.g. `this.x = arg` → assert `instance.getX() == arg`).

For each numbered test case give:
  - description (one sentence)
  - inputs that trigger it
  - expected return / state / exception

Output ONLY a numbered markdown list. No code, no fenced blocks, no preamble."""


# ---------------------------------------------------------------------------
# Prompt 2: generation
# ---------------------------------------------------------------------------

def build_simple_generation_prompt(method: dict, plan: str,
                                   class_inventory: dict | None = None) -> str:
    """Generate the Java test class from the plan, under a strict allowlist."""
    full_name      = method.get("full_name", "")
    class_name     = method.get("class_name", "")
    sig            = method.get("signature", "")
    body           = method.get("body", "")
    javadoc        = method.get("javadoc") or "(none)"
    deps           = method.get("dependency_signatures") or []
    imports        = method.get("source_file_imports") or []
    overload_index = method.get("overload_index")
    test_class     = method["_test_class_name"]
    package        = method["_package"]
    method_name    = method.get("method_name", "")

    overload_callout = ""
    if overload_index is not None:
        overload_callout = (
            f"\n*** OVERLOAD: this is overload index {overload_index}. "
            f"Test ONLY the signature shown above; do NOT write tests for any other "
            f"overload of `{method_name}`. ***"
        )

    sibling_overloads = _format_sibling_overloads(
        class_inventory, full_name, method_name, sig
    )

    return f"""You are writing a single JUnit 5 test class. Output ONLY the complete Java file.

=== METHOD UNDER TEST ===
Class:              {class_name}
Full name:          {full_name}
Package:            {package}
Test class name:    {test_class}    (REQUIRED — the file will be saved under this name)
Signature:          {sig}{overload_callout}

Body:
{body}

Javadoc:
{javadoc}
{sibling_overloads}

=== TEST PLAN (implement one @Test method per item) ===
{plan}

=== ALLOWED CLASSES TO IMPORT (verbatim — copied from the SUT's source file) ===
{_format_imports(imports)}

  (Plus implicitly available: java.lang.*, org.junit.jupiter.api.Test, org.junit.jupiter.api.Assertions.*)

=== ALLOWED EXTERNAL METHOD CALLS (Understand-derived from this method's body) ===
{_format_dep_lines(deps, class_inventory)}

=== ALLOWED PUBLIC API ON THE SUT CLASS ({class_name}) ===
{_format_sut_api(class_inventory, full_name, class_name)}

=== HARD CONSTRAINTS — VIOLATIONS WILL FAIL THE BUILD ===
1. Do NOT invent method names, class names, parameter names, field names, or constants.
   Use ONLY identifiers that appear in this prompt.
2. The ONLY methods you may call are:
     (a) Items listed under "ALLOWED EXTERNAL METHOD CALLS" or "ALLOWED PUBLIC API ON THE SUT CLASS"
     (b) JUnit Jupiter assertions: {", ".join(_JUNIT_ASSERTIONS)}
     (c) Plain language built-ins: java.lang.* (Object.equals, String literals, primitive ops, etc.)
3. The ONLY classes you may import are those listed under "ALLOWED CLASSES TO IMPORT",
   plus org.junit.jupiter.api.Test and org.junit.jupiter.api.Assertions (use a static import).
4. Do NOT import any class from the package `{package}` itself — you are already in that package
   (Checkstyle flags self-package imports as RedundantImport and the build fails).
5. Use INDIVIDUAL static imports for assertions:
     import static org.junit.jupiter.api.Assertions.assertEquals;
     import static org.junit.jupiter.api.Assertions.assertThrows;
   Do NOT use the wildcard form `import static ...Assertions.*;` — PDFBox's Checkstyle
   config disallows wildcard imports and will fail the build.
6. The body of the method under test is shown for context ONLY. Do NOT reference any
   private or protected field, method, constant, or inner class — even when you see them
   in the body. Test ONLY the public surface listed above. Anything not explicitly listed
   under the ALLOWED sections is private / package-private / protected by default.
7. Classes tagged `[ABSTRACT]` or `[INTERFACE]` in the allowlists CANNOT be instantiated
   with `new`. If you need an instance of one, either pass `null` with a cast like
   `(MyAbstract) null`, or look for a concrete subclass listed in the allowlists.
   The same applies if the SUT class itself is tagged ABSTRACT/INTERFACE — your test
   should call its public methods on `null` with a cast (which will throw NPE — assert
   that), or skip exercising those code paths.
8. Class declaration MUST be exactly:
     package {package};
     ... imports ...
     public class {test_class} {{ ... }}
9. Each test method must be public, void, take no arguments, and be annotated `@Test`.
10. Implement one `@Test` method per item in the test plan above. Cover all of them.
11. For null arguments where the method has overloads with the same arity, use a
    parameter-type cast like `(float[]) null` to disambiguate `javac`.
12. If you cannot construct an argument from the allowed list, pass `null` with an
    explicit cast like `(SomeType) null`. Do NOT instantiate types not listed above.
13. Do NOT use Mockito, PowerMock, or any other mocking library — only JUnit 5.

=== PRE-FLIGHT CHECK (do this mentally before submitting) ===
For every line of code you wrote, verify:
  • Every method call `obj.foo(...)` — `foo` appears in either ALLOWED EXTERNAL METHOD
    CALLS or ALLOWED PUBLIC API ON THE SUT CLASS (or it's a JUnit Jupiter assertion).
  • Every class name `X` you wrote — `X` appears in ALLOWED CLASSES TO IMPORT,
    or in the allowlists, or in java.lang.*.
  • Every `new X(...)` — `X` is NOT tagged `[ABSTRACT]` or `[INTERFACE]`, AND the
    constructor signature you used appears verbatim in ALLOWED PUBLIC API or
    ALLOWED EXTERNAL METHOD CALLS.
  • Every field reference `X.SOMETHING` — that constant or static field is documented
    in this prompt. Otherwise it's private or invented; remove it.
If any line fails this check, remove or rewrite it before submitting.

Output: the complete Java test class file inside a single ```java ... ``` fenced block.
No prose before or after the fence."""


# ---------------------------------------------------------------------------
# Prompt 3: retry
# ---------------------------------------------------------------------------

def build_simple_retry_prompt(method: dict, failing_test: str,
                              error_message: str,
                              class_inventory: dict | None = None) -> str:
    """Fix a compile or test failure, allowlist still applies."""
    full_name   = method.get("full_name", "")
    class_name  = method.get("class_name", "")
    sig         = method.get("signature", "")
    deps        = method.get("dependency_signatures") or []
    imports     = method.get("source_file_imports") or []
    test_class  = method["_test_class_name"]
    package     = method["_package"]

    return f"""The previous test you wrote did not pass. Fix it.

=== METHOD UNDER TEST ===
Class:           {class_name}
Full name:       {full_name}
Signature:       {sig}
Test class name: {test_class}    (do not rename)
Package:         {package}        (do not change)

=== FAILING TEST (your previous output) ===
```java
{failing_test}
```

=== ERROR ===
{error_message}

=== ALLOWED CLASSES TO IMPORT ===
{_format_imports(imports)}

=== ALLOWED EXTERNAL METHOD CALLS ===
{_format_dep_lines(deps, class_inventory)}

=== ALLOWED PUBLIC API ON THE SUT CLASS ===
{_format_sut_api(class_inventory, full_name, class_name)}

=== INSTRUCTIONS ===
1. Read the error message carefully and fix ONLY what it points to.
2. Do NOT introduce new tests or restructure unrelated code.
3. Do NOT invent method names, class names, parameter names, or constants.
4. Same allowlist applies: only the methods, classes, and assertions listed here.
5. Same package, same test class name. Do NOT import any class from `{package}`.
6. For null arguments where an overload-set has the same arity, cast like `(float[]) null`.
7. If checkstyle flagged `RedundantImport`, remove the offending self-package import line.
8. If checkstyle flagged a wildcard import (e.g. `import static ...Assertions.*`), replace
   it with individual static imports for each assertion you actually use.
9. If javac said "cannot find symbol", the called method/field is not on the allowlist —
   remove that line or replace it with a method that IS on the allowlist.
10. If javac said "has private/protected access", you accessed a non-public member.
    Remove that access; only public members may be touched from a test in another package.
11. If javac said "is abstract; cannot be instantiated", change `new SomeAbstractClass(...)`
    to either `(SomeAbstractClass) null` or to a concrete subclass listed in the allowlists.
12. If javac said "no suitable constructor/method found", check the parameter types of
    your call against the signature in the allowlist — fix the arg types or wrap nulls
    with a type cast.

Output ONLY the corrected Java test class inside a single ```java ... ``` fenced block.
No prose before or after the fence."""
