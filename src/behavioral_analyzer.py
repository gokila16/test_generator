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

# Matches the start of an if-block.
_IF_RE = re.compile(r'\bif\s*\(')

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


def _balance_parens(body: str, open_pos: int) -> int:
    """
    Starting at the opening '(' at *open_pos*, return the index just past the
    matching ')'.  Returns -1 if the parens are unbalanced.
    """
    depth = 1
    i = open_pos + 1
    while i < len(body) and depth > 0:
        if body[i] == '(':
            depth += 1
        elif body[i] == ')':
            depth -= 1
        i += 1
    return i if depth == 0 else -1


def _outcome_in_window(body: str, start: int, window: int = 300) -> str:
    """
    Scan up to *window* characters from *start* for the first throw or literal
    return and return a description string, or 'continues execution' if neither
    is found.
    """
    snippet = body[start:start + window]
    throw_m  = re.search(r'throw\s+new\s+([A-Z][A-Za-z0-9_]*)', snippet)
    return_m = re.search(r'\breturn\s+(' + _LITERAL_PAT + r')', snippet)

    if throw_m and return_m:
        if throw_m.start() < return_m.start():
            return f"throws {throw_m.group(1)}"
        return f"returns {return_m.group(1)}"
    if throw_m:
        return f"throws {throw_m.group(1)}"
    if return_m:
        return f"returns {return_m.group(1)}"

    # Check for a plain 'return;' (void method)
    if re.search(r'\breturn\s*;', snippet):
        return "returns (void)"

    return "continues execution"


def extract_branches(method_body: str) -> list:
    """
    Statically extract the if-condition branches from *method_body*.

    Returns a list of dicts:
        {
          "condition":      "x == null",
          "taken":          "throws IllegalArgumentException",
          "not_taken":      "continues execution"   # or None when unknown
        }

    Only if-blocks are captured.  The list preserves source order and is
    capped at 10 branches so the prompt section stays concise.
    """
    if not method_body:
        return []

    branches = []
    for m in _IF_RE.finditer(method_body):
        # Position of the '(' that opens the condition.
        paren_open = m.end() - 1   # _IF_RE ends just past '('
        close = _balance_parens(method_body, paren_open)
        if close == -1:
            continue

        condition = method_body[paren_open + 1: close - 1].strip()
        if not condition:
            continue

        # What happens immediately inside the if-block (taken path)?
        taken = _outcome_in_window(method_body, close)

        # What happens after the if-block (not-taken path)?
        # Scan forward past the closing brace of the if-body.
        after_start = close
        brace_m = re.search(r'\{', method_body[close:])
        if brace_m:
            brace_pos = close + brace_m.start()
            depth = 1
            i = brace_pos + 1
            while i < len(method_body) and depth > 0:
                if method_body[i] == '{':
                    depth += 1
                elif method_body[i] == '}':
                    depth -= 1
                i += 1
            after_start = i

        # Check for else / else-if
        else_m = re.match(r'\s*else\s*', method_body[after_start:after_start + 30])
        if else_m:
            not_taken = _outcome_in_window(method_body, after_start + else_m.end())
        else:
            not_taken = None   # unknown without else

        branches.append({
            "condition": condition,
            "taken":     taken,
            "not_taken": not_taken,
        })

        if len(branches) >= 10:
            break

    return branches
