def build_base_prompt(method):
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package         = '.'.join(method['full_name'].split('.')[:-2])
    class_name      = method['class_name']
    method_name     = method['method_name']

    prompt = f"""You are a senior SDET (Software Development Engineer in Test) writing a JUnit 5 test class for ONE Java method.

YOUR METHODOLOGY — path-based testing:
You read the implementation to understand EVERY path the method can take. Then you design
tests that EXERCISE each path from the OUTSIDE: you craft an input that forces the method
down a specific path, and you assert the known outcome for that path.

Work in this order:
1. READ the implementation below and map out EVERY path the method can take:
   - every branch (if / else / else-if / switch / ternary)
   - every guard clause and early return
   - every loop boundary (zero iterations, one iteration, many iterations)
   - every exception that is thrown or propagated
   - every null / empty / zero / negative / boundary case the code actually handles
2. For EACH path, write ONE focused @Test that forces the method down that exact path
   THROUGH ITS PUBLIC SIGNATURE, and asserts the concrete outcome the implementation
   guarantees for that path (the exact return value, the resulting state, or the exact
   exception type).
3. Name each @Test method after the path/scenario it covers (e.g. returnsZero_whenInputEmpty).

TARGET METHOD — this is the ONLY method you are allowed to test:
  Class    : {class_name}
  Method   : {method_name}
  Signature: {method.get('signature', '')}

// Method Implementation (this is your ONLY source of truth for expected behavior):
{method.get('body', '')}
"""

    if method.get('javadoc'):
        prompt += f"\n// Documentation Comment:\n{method['javadoc']}\n"

    prompt += f"""
ANTI-HALLUCINATION RULES (mandatory — breaking any of these makes the test worthless):
- Test ONLY the target method `{method_name}`. Do not test any other method.
- Derive every expected result ONLY by reading the implementation above. Do NOT guess,
  assume, or invent behavior that the code does not actually show.
- Use ONLY: standard Java/JDK classes, JUnit 5 APIs, and the exact types that already
  appear in the signature and implementation above.
- Do NOT invent, rename, abbreviate, or guess any class name, constructor, method name,
  field name, enum constant, or package. If it is not shown above and is not part of
  standard Java/JUnit, you may not use it.
- Do NOT call private or helper methods directly. Reach every path by calling the public
  target method `{method_name}` only.
- If `{method_name}` is static, call it statically as {class_name}.{method_name}(...).
  If it is an instance method, construct the object using ONLY a constructor that is
  evident from the code; if no constructor is visible, use `new {class_name}()`.
- For expected exceptions, use assertThrows with the EXACT exception type the
  implementation throws — do not substitute a more general type.

ASSERTION QUALITY:
- Every @Test must contain at least one meaningful assertion of a CONCRETE expected value
  that you computed by reading the implementation.
- FORBIDDEN: assertTrue(true), empty test bodies, or using assertNotNull as the only
  assertion.
- Prefer assertEquals / assertArrayEquals / assertThrows / assertSame with exact expected
  values.

OUTPUT FORMAT (strict):
- The test class MUST be named exactly: {test_class_name}
- The package MUST be exactly: {package}
- Include ALL necessary imports.
- Output ONLY the complete, compilable Java file inside a single ```java ... ``` code block.
  No explanation and no text outside the code block.

Now write the JUnit 5 test class:"""
    return prompt


def build_retry_prompt(error_message, failing_test, method):
    test_class_name = f"{method['class_name']}_{method['method_name']}_Test"
    package         = '.'.join(method['full_name'].split('.')[:-2])
    class_name      = method['class_name']
    method_name     = method['method_name']

    return f"""The JUnit 5 test you generated for `{method_name}` FAILED. Fix it.

Error output:
{error_message}

Here is the failing test:
{failing_test}

TARGET METHOD — the ONLY method under test:
  Class    : {class_name}
  Method   : {method_name}
  Signature: {method.get('signature', '')}

// Method Implementation (your ONLY source of truth):
{method.get('body', '')}

HOW TO FIX (in priority order):
1. Read the error. If it is a COMPILE error (e.g. "cannot find symbol", unknown class or
   method), you almost certainly invented or misspelled a name. Remove it and use ONLY
   classes, constructors, methods, and fields that actually appear in the implementation
   above, or that belong to standard Java/JUnit 5.
2. If it is an ASSERTION FAILURE, your expected value was wrong. Re-read the
   implementation, recompute the correct expected value, and fix the assertion. Do NOT
   delete the assertion and do NOT weaken it to assertTrue(true).

STRICT RULES (unchanged):
- Test ONLY `{method_name}`, reaching every path through its public signature.
- Do NOT invent or guess any class / method / field / enum / package name. Derive expected
  behavior ONLY from the implementation shown.
- Keep meaningful assertions of concrete expected values; never replace them with trivial ones.
- Class name MUST be exactly: {test_class_name}
- Package MUST be exactly: {package}
- Output ONLY the corrected, complete Java file inside a single ```java ... ``` code block.
  No text outside the code block.

Fixed test:"""
