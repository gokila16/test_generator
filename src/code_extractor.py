import re

def extract_java_code(response):
    """
    Extracts Java code from LLM response.
    Tries multiple patterns in order.
    Returns clean Java code string or None.
    """
    if not response:
        return None

    # Pattern 1: ```java ... ```
    match = re.search(
        r'```java\s*(.*?)\s*```',
        response,
        re.DOTALL
    )
    if match:
        return match.group(1).strip()

    # Pattern 2: ``` ... ```
    match = re.search(
        r'```\s*(.*?)\s*```',
        response,
        re.DOTALL
    )
    if match:
        return match.group(1).strip()

    # Pattern 3: starts with import or public class
    match = re.search(
        r'(import\s+[\w.]+;|public\s+class\s+\w+)',
        response
    )
    if match:
        return response[match.start():].strip()

    return None