import re
import config
from src.resource_scanner import is_file_dependent


def compute_clc(body: str) -> int:
    """
    Computes a cyclomatic-like complexity score for a Java method body.
    Strips comments and string/char literals first to avoid counting
    keywords that appear inside them.
    """
    # Remove block comments (/* ... */)
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.DOTALL)
    # Remove line comments (// ...)
    body = re.sub(r'//[^\n]*', '', body)
    # Replace string literals with empty strings (handles escaped quotes)
    body = re.sub(r'"(?:[^"\\]|\\.)*"', '""', body)
    # Replace char literals
    body = re.sub(r"'(?:[^'\\]|\\.)*'", "''", body)

    # Count branching constructs
    keyword_hits = len(re.findall(r'\b(if|else\s+if|for|while|do|case|catch)\b', body))
    ternary_hits = len(re.findall(r'\?', body))
    return keyword_hits + ternary_hits


def has_external_dependencies(method: dict, dep_chains: dict) -> bool:
    """
    Returns True if the method needs richer test scaffolding due to either:

    1. Unresolvable dependency types — at least one parameter or receiver in
       the dependency chain has strategy 'unknown' or 'unresolvable_abstract',
       meaning the build system cannot construct it automatically.

    2. File-system dependent parameters — the method signature contains a
       file-related type (File, Path, InputStream, RandomAccessRead, etc.).
       These require real test resource files and proper stream wrapping,
       which the checklist + resource agents handle better than the simple path.
       Detection delegates to is_file_dependent() in resource_scanner.py so
       the set of file-system types is maintained in one place.
    """
    # Check 2 first — it's a cheap string scan
    if is_file_dependent(method):
        return True

    key = f"{method.get('full_name', '')}|{method.get('signature', '')}"
    chain = dep_chains.get(key)
    if not chain:
        return False

    _EXTERNAL_STRATEGIES = {'unknown', 'unresolvable_abstract'}

    receiver = chain.get('receiver')
    if receiver and receiver.get('strategy') in _EXTERNAL_STRATEGIES:
        return True

    for p in chain.get('params') or []:
        if p.get('strategy') in _EXTERNAL_STRATEGIES:
            return True

    return False


def classify(method: dict, dep_chains: dict, threshold: int = None) -> str:
    """
    Routes a method to either the 'simple' or 'complex' generation path.

    Complex if:
      - CLC of method body >= threshold (default config.CLC_THRESHOLD), OR
      - method has at least one unresolvable external dependency type, OR
      - method signature contains a file-system dependent parameter type

    Returns 'simple' or 'complex'.
    """
    if threshold is None:
        threshold = config.CLC_THRESHOLD

    clc = compute_clc(method.get('body', ''))
    if clc >= threshold:
        return 'complex'

    if has_external_dependencies(method, dep_chains):
        return 'complex'

    return 'simple'
