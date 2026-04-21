"""
src/behavioral_analyzer.py

Parses a Java method body string with regex to extract concrete behavioral
contracts (thrown exceptions and literal return values) that the LLM can use
as precise test-assertion targets rather than guessing from signatures.
"""

import re

# Matches "throw new SomeException(" — re-throws ("throw e;") are excluded.
_THROW_RE = re.compile(r'throw\s+new\s+([A-Z][A-Za-z0-9_]*)\s*\(')

# Matches "return <literal>" where literal is a string, boolean, null, or
# numeric (integer or decimal, with optional Java type suffix).
_LITERAL_PAT = r'"(?:[^"\\]|\\.)*"|true\b|false\b|null\b|-?\d+(?:\.\d+)?[fFdDlL]?\b'
_RETURN_RE   = re.compile(r'\breturn\s+(' + _LITERAL_PAT + r')', re.MULTILINE)

# Window (in characters) before a throw site to search for a guarding if.
_CONDITION_LOOKBACK = 200


def _extract_if_condition(body: str, throw_start: int):
    """
    Search the _CONDITION_LOOKBACK characters before *throw_start* for the
    nearest ``if (…)`` guard and return the balanced condition text, or None
    if no guard is found.
    """
    window_start = max(0, throw_start - _CONDITION_LOOKBACK)
    window = body[window_start:throw_start]

    # Find the last 'if (' in the window (most immediately enclosing guard).
    last_if = None
    for m in re.finditer(r'if\s*\(', window):
        last_if = m

    if last_if is None:
        return None

    # Position of the opening '(' in the full body string.
    paren_pos = window_start + last_if.end() - 1   # last_if.end() is past '('

    # Walk forward, balancing parentheses.
    depth = 1
    i = paren_pos + 1
    while i < len(body) and depth > 0:
        if body[i] == '(':
            depth += 1
        elif body[i] == ')':
            depth -= 1
        i += 1

    if depth != 0:          # unbalanced — malformed source, skip
        return None

    condition = body[paren_pos + 1 : i - 1].strip()
    return condition if condition else None


def _get_line(body: str, pos: int) -> str:
    """Return the source line that contains *pos*, stripped of leading whitespace."""
    line_start = body.rfind('\n', 0, pos) + 1          # 0 if no preceding newline
    line_end   = body.find('\n', pos)
    if line_end == -1:
        line_end = len(body)
    return body[line_start:line_end].strip()


def extract_behavioral_constraints(method_body: str) -> dict:
    """
    Parse *method_body* and return a dict describing the concrete behaviors
    that are directly readable from the source:

    {
      "throws": [
        {"exception": "IOException", "condition": "fileName == null"}
      ],
      "returns": [
        {"value": "true", "context": "return true;"}
      ]
    }

    ``condition`` is None when no enclosing ``if`` guard is found within
    _CONDITION_LOOKBACK characters.  The caller should decide whether to
    surface condition-less throws.

    Returns ``{"throws": [], "returns": []}`` for falsy input.
    """
    if not method_body:
        return {"throws": [], "returns": []}

    throws  = []
    returns = []

    for m in _THROW_RE.finditer(method_body):
        exception = m.group(1)
        condition = _extract_if_condition(method_body, m.start())
        throws.append({"exception": exception, "condition": condition})

    for m in _RETURN_RE.finditer(method_body):
        value   = m.group(1)
        context = _get_line(method_body, m.start())
        returns.append({"value": value, "context": context})

    return {"throws": throws, "returns": returns}
