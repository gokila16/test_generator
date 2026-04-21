import re

def extract_java_code(response):
    """
    Extracts Java code from LLM response.
    Tries multiple patterns in order.
    Returns (code, None) on success, (None, "no_code_block") when no code
    block can be found.
    """
    if not response:
        return None, "no_code_block"

    if "@Test" not in response:
        return None, "no_test_annotation"

    # Pattern 1: ```java ... ```
    match = re.search(
        r'```java\s*(.*?)\s*```',
        response,
        re.DOTALL
    )
    if match:
        return match.group(1).strip(), None

    # Pattern 2: ``` ... ```
    match = re.search(
        r'```\s*(.*?)\s*```',
        response,
        re.DOTALL
    )
    if match:
        return match.group(1).strip(), None

    # Pattern 3: starts with package, import, or public class
    match = re.search(
        r'(package\s+[\w.]+;|import\s+[\w.]+;|public\s+class\s+\w+)',
        response
    )
    if match:
        return response[match.start():].strip(), None

    return None, "no_code_block"