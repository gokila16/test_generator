"""
src/behavioral_analyzer.py

Parses a Java method body via the `javalang` AST to extract concrete behavioral
contracts (thrown exceptions, literal return values, branch outcomes) that the
LLM can use as precise test-assertion targets rather than guessing from
signatures.

Why AST instead of regex:
  - String/char literals containing "throw new", "return", or "if (" are no
    longer mistaken for code (the lexer handles them).
  - Block and line comments are skipped automatically.
  - Throw guards are determined by AST containment, not by text proximity, so
    a sibling `if` near (but not enclosing) a throw is not misattributed.

Public API (unchanged):
  - extract_behavioral_constraints(method_body) -> dict
  - extract_branches(method_body)               -> list
"""

import javalang
from javalang.tree import (
    BlockStatement,
    ClassCreator,
    IfStatement,
    Literal,
    MethodDeclaration,
    ReturnStatement,
    ThrowStatement,
)

_MAX_BRANCHES = 10


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_body(method_body):
    """
    Wrap *method_body* in a dummy class+method and parse with javalang.

    Returns (MethodDeclaration node, wrapped_source) on success, or
    (None, None) if parsing fails.  The wrapped source is returned so callers
    can use AST positions to slice condition text verbatim.
    """
    if not method_body or not method_body.strip():
        return None, None

    body = method_body.strip()
    if not body.startswith('{'):
        body = '{ ' + body + ' }'

    # `throws Throwable` keeps javalang from rejecting unhandled checked
    # exceptions in arbitrary snippets we receive.
    wrapped = f'class _W_ {{ void _m_() throws Throwable {body} }}'

    try:
        tree = javalang.parse.parse(wrapped)
    except (javalang.parser.JavaSyntaxError,
            javalang.tokenizer.LexerError,
            Exception):  # noqa: BLE001 — broad catch is intentional; never raise on bad input
        return None, None

    for _, node in tree.filter(MethodDeclaration):
        return node, wrapped
    return None, None


def _line_col_to_offset(source, line, col):
    """Convert 1-indexed (line, col) to a 0-indexed character offset."""
    offset = 0
    cur_line = 1
    while cur_line < line:
        nl = source.find('\n', offset)
        if nl == -1:
            return -1
        offset = nl + 1
        cur_line += 1
    return offset + (col - 1)


def _balanced_paren_close(source, open_idx):
    """
    Walk forward from `(` at *open_idx* and return the index just past the
    matching `)`.  Skips string literals, char literals, and comments so that
    parens inside those are not counted.  Returns -1 if unbalanced.
    """
    depth = 1
    i = open_idx + 1
    n = len(source)
    while i < n and depth > 0:
        c = source[i]

        # Line comment
        if c == '/' and i + 1 < n and source[i + 1] == '/':
            nl = source.find('\n', i)
            i = n if nl == -1 else nl + 1
            continue
        # Block comment
        if c == '/' and i + 1 < n and source[i + 1] == '*':
            end = source.find('*/', i + 2)
            i = n if end == -1 else end + 2
            continue
        # String literal
        if c == '"':
            i += 1
            while i < n and source[i] != '"':
                i += 2 if source[i] == '\\' else 1
            i += 1
            continue
        # Char literal
        if c == "'":
            i += 1
            while i < n and source[i] != "'":
                i += 2 if source[i] == '\\' else 1
            i += 1
            continue

        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        i += 1

    return i if depth == 0 else -1


def _condition_text(source, if_node):
    """
    Verbatim if-condition text from *source*, anchored on the AST position of
    *if_node*.  Returns the text inside the parens (stripped) or None.
    """
    if if_node.position is None:
        return None
    offset = _line_col_to_offset(source, if_node.position.line, if_node.position.column)
    if offset < 0:
        return None
    paren_open = source.find('(', offset)
    if paren_open == -1:
        return None
    paren_close = _balanced_paren_close(source, paren_open)
    if paren_close == -1:
        return None
    return source[paren_open + 1:paren_close - 1].strip() or None


def _line_at_offset(source, offset):
    """Return the source line containing *offset*, stripped of surrounding whitespace."""
    line_start = source.rfind('\n', 0, offset) + 1
    line_end = source.find('\n', offset)
    if line_end == -1:
        line_end = len(source)
    return source[line_start:line_end].strip()


def _enclosing_if(path):
    """Innermost IfStatement ancestor in *path*, or None if there is none."""
    for ancestor in reversed(path):
        if isinstance(ancestor, IfStatement):
            return ancestor
    return None


def _is_throw_new(node):
    """True iff *node* is `throw new X(...)` (excludes plain re-throws like `throw e`)."""
    return (
        isinstance(node, ThrowStatement)
        and isinstance(node.expression, ClassCreator)
        and node.expression.type is not None
        and node.expression.type.name
    )


def _summarize_outcome(stmt):
    """
    Describe the immediate outcome of *stmt* (the body of an if-branch).

    Returns one of:
        "throws X"
        "returns <literal>"
        "returns (void)"
        "continues execution"
    """
    if stmt is None:
        return "continues execution"

    # Walk into the first statement of a block.
    while isinstance(stmt, BlockStatement) and stmt.statements:
        stmt = stmt.statements[0]

    if isinstance(stmt, ThrowStatement):
        if (isinstance(stmt.expression, ClassCreator)
                and stmt.expression.type is not None
                and stmt.expression.type.name):
            return f"throws {stmt.expression.type.name}"
        return "continues execution"  # bare re-throw — no concrete contract

    if isinstance(stmt, ReturnStatement):
        if stmt.expression is None:
            return "returns (void)"
        if isinstance(stmt.expression, Literal):
            return f"returns {stmt.expression.value}"
        return "continues execution"  # non-literal return — no concrete contract

    return "continues execution"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_behavioral_constraints(method_body):
    """
    Parse *method_body* and return:

        {
          "throws":  [{"exception": str, "condition": str | None}, ...],
          "returns": [{"value":     str, "context":   str},        ...],
        }

    `condition` is None for throws that have no enclosing `if` guard.
    `value` is the literal token as it appears in source (booleans/null/numbers
    plain; strings include their surrounding quotes).
    Returns the empty shape on falsy input or unparseable source.
    """
    empty = {"throws": [], "returns": []}
    if not method_body:
        return empty

    method, source = _parse_body(method_body)
    if method is None:
        return empty

    throws = []
    for path, node in method.filter(ThrowStatement):
        if not _is_throw_new(node):
            continue
        guard = _enclosing_if(path)
        condition = _condition_text(source, guard) if guard else None
        throws.append({
            "exception": node.expression.type.name,
            "condition": condition,
        })

    returns = []
    for _, node in method.filter(ReturnStatement):
        expr = node.expression
        if not isinstance(expr, Literal):
            continue
        if node.position is not None:
            offset = _line_col_to_offset(source, node.position.line, node.position.column)
            context = _line_at_offset(source, offset) if offset >= 0 else f"return {expr.value};"
        else:
            context = f"return {expr.value};"
        returns.append({"value": expr.value, "context": context})

    return {"throws": throws, "returns": returns}


def extract_branches(method_body):
    """
    Statically extract if-branches from *method_body*.

    Returns a list of dicts (capped at _MAX_BRANCHES, source order preserved):
        {"condition": str, "taken": str, "not_taken": str | None}

    `not_taken` is None when the if has no else clause.  Else-if chains report
    the inner if as another entry in source order; the outer's not_taken stays
    None because the AST else_statement is itself an IfStatement (no concrete
    outcome at that level).
    """
    if not method_body:
        return []

    method, source = _parse_body(method_body)
    if method is None:
        return []

    branches = []
    for _, if_node in method.filter(IfStatement):
        condition = _condition_text(source, if_node)
        if not condition:
            continue

        taken = _summarize_outcome(if_node.then_statement)
        if if_node.else_statement is None:
            not_taken = None
        elif isinstance(if_node.else_statement, IfStatement):
            # else-if — outer's "else" leads into another conditional; no
            # single concrete outcome here.  The inner if appears as its own
            # entry on the next iteration of the filter walk.
            not_taken = None
        else:
            not_taken = _summarize_outcome(if_node.else_statement)

        branches.append({
            "condition": condition,
            "taken":     taken,
            "not_taken": not_taken,
        })
        if len(branches) >= _MAX_BRANCHES:
            break

    return branches
