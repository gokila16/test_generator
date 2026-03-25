def build_base_prompt(method):
    test_class_name = f"{method['class_name']}Test"
    
    prompt = f"""You are a Java unit test generator. Generate a JUnit 5 test for the following method.
Your test should:
- Verify the functional correctness of the method.
- Use meaningful variable names.
- Include at least one meaningful assertion (not just assert(true) or assertNotNull alone).
- Cover typical usage scenarios.
- Be compilable and runnable as a standalone JUnit 5 test.

IMPORTANT: The test class MUST be named exactly: {test_class_name}
IMPORTANT: The package MUST be: {'.'.join(method['full_name'].split('.')[:-2])}

// Method Signature:
{method.get('signature', '')}

// Method Implementation:
{method.get('body', '')}
"""
    if method.get('javadoc'):
        prompt += f"\n// Documentation Comment:\n{method['javadoc']}\n"

    prompt += "\nNow generate the JUnit 5 test:"
    return prompt


def build_retry_prompt(error_message, failing_test, method):
    test_class_name = f"{method['class_name']}Test"
    package = '.'.join(method['full_name'].split('.')[:-2])
    
    return f"""The test you generated previously failed to compile with the following error:
{error_message}

Here is the failing test:
{failing_test}

Please fix the test. Follow these strict rules:
- Class name MUST be: {test_class_name}
- Package MUST be: {package}
- Only use standard Java classes or classes visible in the method implementation above
- Do NOT invent or guess class names
- If you cannot write a meaningful test, write the simplest possible test that compiles
- The method signature is: {method.get('signature', '')}

// Fixed test:"""