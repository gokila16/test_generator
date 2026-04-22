"""
src/cfg_slicer.py

Behavioral slicing module for Java method bodies.

Takes a method body string + metadata and produces a list of independently
testable behavioral slices.  Each slice represents one observable behavior:
a throw path, a return path, a state mutation, a loop iteration count, or a
branch outcome.

Uses javalang for AST-level analysis when available; falls back to regex for
constructs javalang cannot handle (ternaries, complex conditions, inline returns).
"""

import re
import string
from typing import Optional

try:
    import javalang as _javalang_mod
    _JAVALANG = True
except ImportError:  # pragma: no cover
    _JAVALANG = False


# ─── slice ID generator ────────────────────────────────────────────────────

def _slice_ids():
    """Yields A, B, C, …, Z, AA, AB, … deterministically."""
    for letter in string.ascii_uppercase:
        yield letter
    for prefix in string.ascii_uppercase:
        for suffix in string.ascii_uppercase:
            yield prefix + suffix


# ─── regex patterns ────────────────────────────────────────────────────────

# throw new ExceptionType(ctorArgs);
_THROW_RE = re.compile(
    r'\bthrow\s+new\s+([A-Z][A-Za-z0-9_]*)\s*\(([^;]*?)\)\s*;',
    re.DOTALL,
)

# return <literal>;  (string / char / boolean / null / numeric)
_RETURN_LITERAL_RE = re.compile(
    r'\breturn\s+'
    r'('
    r'(?:"(?:[^"\\]|\\.)*")'          # "string"
    r"|(?:'(?:[^'\\]|\\.)*')"          # 'c'
    r'|true\b|false\b|null\b'          # boolean/null
    r'|(?:-?\d+(?:\.\d+)?[fFdDlL]?\b)'  # numeric
    r')\s*;',
    re.MULTILINE,
)

# return new Type(
_RETURN_NEW_RE = re.compile(
    r'\breturn\s+new\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',
    re.MULTILINE,
)

# this.field = (assignment, not comparison)
_FIELD_ASSIGN_RE = re.compile(r'this\.(\w+)\s*=[^=]')

# if (
_IF_OPEN_RE = re.compile(r'\bif\s*\(')

# any loop head
_LOOP_RE = re.compile(r'\b(?:for|while)\s*\(')

# enhanced for: for (Type var : collectionExpr)
_ENHANCED_FOR_RE = re.compile(
    r'\bfor\s*\(\s*(?:final\s+)?(?:[\w<>\[\],\s]+?)\s+(\w+)\s*:\s*(\w[\w.]*)\s*\)'
)

# return type from method signature  (first token(s) before method name + '(')
_RETURN_TYPE_RE = re.compile(
    r'^(?:(?:public|protected|private|static|final|abstract|synchronized|native'
    r'|default|strictfp)\s+)*'
    r'([\w<>\[\],\s]+?)\s+\w+\s*\(',
)


# ─── guard condition extraction ────────────────────────────────────────────

def _nearest_if_condition(body: str, pos: int, lookback: int = 300) -> Optional[str]:
    """
    Finds the nearest enclosing ``if (…)`` guard that precedes *pos* within
    *lookback* characters, and returns the balanced condition text, or None.
    """
    window_start = max(0, pos - lookback)
    window = body[window_start:pos]

    last_if = None
    for m in _IF_OPEN_RE.finditer(window):
        last_if = m

    if last_if is None:
        return None

    # Absolute position of the opening '(' in the full body string.
    abs_open = window_start + last_if.end() - 1
    depth = 1
    i = abs_open + 1
    while i < len(body) and depth > 0:
        c = body[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        i += 1
    if depth != 0:
        return None
    cond = body[abs_open + 1: i - 1].strip()
    return cond if cond else None


def _source_line(body: str, pos: int) -> str:
    """Returns the source line that contains *pos*, stripped of whitespace."""
    s = body.rfind('\n', 0, pos) + 1
    e = body.find('\n', pos)
    return body[s:(len(body) if e == -1 else e)].strip()


# ─── signature helpers ─────────────────────────────────────────────────────

def _parse_params(signature: str) -> list:
    """
    Parses ``(Type name, ...)`` from a method signature.
    Returns list of (type_str, name_str) tuples.
    """
    m = re.search(r'\(([^)]*)\)', signature)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw:
        return []
    result = []
    for part in raw.split(','):
        tokens = part.strip().split()
        if len(tokens) >= 2:
            # Handle annotations like @Nullable String name
            name = tokens[-1]
            type_tokens = [t for t in tokens[:-1] if not t.startswith('@')]
            result.append((' '.join(type_tokens), name))
    return result


def _method_name_from_sig(signature: str) -> str:
    """Extracts the method name from a signature string."""
    m = re.search(r'\b(\w+)\s*\(', signature)
    return m.group(1) if m else 'method'


# ─── class inventory helpers ───────────────────────────────────────────────

def _inventory_entry(simple_name: str, inventory: dict) -> Optional[dict]:
    """Looks up an inventory entry by simple class name."""
    for full, entry in inventory.items():
        cn = entry.get('class_name') or full.split('.')[-1]
        if cn == simple_name:
            return entry
    return None


def _construction(simple_type: str, inventory: dict) -> tuple:
    """
    Returns ``(java_expr, confidence)`` for constructing *simple_type*.

    confidence values: 'high' | 'medium' | 'low'

    Validates every constructor/factory against class_inventory before use.
    Never returns a hallucinated constructor.
    """
    # Strip generic parameters for lookup
    base_type = re.sub(r'<[^>]*>', '', simple_type).strip().rstrip('[]')

    WELL_KNOWN = {
        'int': '0', 'long': '0L', 'double': '0.0', 'float': '0.0f',
        'boolean': 'false', 'byte': '(byte)0', 'short': '(short)0',
        'char': "'\\0'",
        'String': '""',
        'Integer': '0', 'Long': '0L', 'Double': '0.0', 'Float': '0.0f',
        'Boolean': 'false', 'Object': 'new Object()',
        'List': 'new java.util.ArrayList<>()',
        'ArrayList': 'new java.util.ArrayList<>()',
        'LinkedList': 'new java.util.LinkedList<>()',
        'Map': 'new java.util.HashMap<>()',
        'HashMap': 'new java.util.HashMap<>()',
        'LinkedHashMap': 'new java.util.LinkedHashMap<>()',
        'Set': 'new java.util.HashSet<>()',
        'HashSet': 'new java.util.HashSet<>()',
        'LinkedHashSet': 'new java.util.LinkedHashSet<>()',
        'StringBuilder': 'new StringBuilder()',
        'StringBuffer': 'new StringBuffer()',
        'ByteArrayOutputStream': 'new java.io.ByteArrayOutputStream()',
        'ByteArrayInputStream': 'new java.io.ByteArrayInputStream(new byte[0])',
        'File': 'new java.io.File(".")',
        'Date': 'new java.util.Date()',
    }
    if base_type in WELL_KNOWN:
        return WELL_KNOWN[base_type], 'high'

    entry = _inventory_entry(base_type, inventory)
    if not entry:
        return f'/* TODO: construct {base_type} */', 'low'

    if entry.get('is_abstract') or entry.get('is_interface'):
        subs = entry.get('concrete_subclasses', [])
        if subs:
            sub_simple = subs[0].split('.')[-1]
            sub_entry = _inventory_entry(sub_simple, inventory)
            if sub_entry:
                sub_expr, sub_conf = _construction(sub_simple, inventory)
                return sub_expr, 'medium'
        return (
            f'/* TODO: {base_type} is abstract/interface — '
            f'pick a concrete subclass from class_inventory */',
            'low',
        )

    ctors = [
        c for c in entry.get('constructors', [])
        if c.get('visibility') in ('public', 'protected')
    ]
    if not ctors:
        fms = entry.get('factory_methods', [])
        if fms:
            fm = fms[0]
            arg_exprs = []
            conf = 'medium'
            for p in fm.get('params', []):
                simple_p = re.sub(r'<[^>]*>', '', p.split('.')[-1]).strip()
                expr, c = _construction(simple_p, inventory)
                if c == 'low':
                    conf = 'low'
                arg_exprs.append(expr)
            return f'{base_type}.{fm["name"]}({", ".join(arg_exprs)})', conf
        return f'/* TODO: no public constructor for {base_type} */', 'low'

    # Prefer no-arg constructor
    for c in ctors:
        if not c.get('params'):
            return f'new {base_type}()', 'high'

    # Use first available constructor, building args recursively
    c = ctors[0]
    arg_exprs = []
    conf = 'medium'
    for p in c.get('params', []):
        simple_p = re.sub(r'<[^>]*>', '', p.split('.')[-1]).strip()
        expr, c_conf = _construction(simple_p, inventory)
        if c_conf == 'low':
            conf = 'low'
        arg_exprs.append(expr)
    return f'new {base_type}({", ".join(arg_exprs)})', conf


# ─── recipe builders ───────────────────────────────────────────────────────

def _recipe(params: list, inventory: dict,
            hints: Optional[dict] = None) -> tuple:
    """
    Builds a ``{param_name: java_expr}`` recipe dict for the given params.

    *hints* allows forcing specific values for named params (e.g. null for
    null-check guards).  Returns ``(recipe_dict, overall_confidence)``.
    """
    hints = hints or {}
    recipe = {}
    overall = 'high'
    for ptype, pname in params:
        if pname in hints:
            recipe[pname] = hints[pname]
        else:
            base = re.sub(r'<[^>]*>', '', ptype.split('.')[-1]).strip().rstrip('[]')
            expr, conf = _construction(base, inventory)
            if conf == 'low':
                overall = 'low'
            elif conf == 'medium' and overall == 'high':
                overall = 'medium'
            recipe[pname] = expr
    return recipe, overall


def _condition_hints(condition: str, params: list) -> dict:
    """
    Derives forced param values from a guard condition.

    ``key == null``         → ``{"key": "null"}``
    ``value.isEmpty()``     → ``{"value": '""'}``
    ``len.length() == 0``   → ``{"len": '""'}``
    """
    if not condition:
        return {}
    param_names = {n for _, n in params}
    hints = {}
    for name in re.findall(r'\b(\w+)\s*==\s*null', condition):
        if name in param_names:
            hints[name] = 'null'
    for name in re.findall(r'\bnull\s*==\s*(\w+)', condition):
        if name in param_names:
            hints[name] = 'null'
    for name in re.findall(r'\b(\w+)\s*\.\s*isEmpty\s*\(\s*\)', condition):
        if name in param_names:
            hints[name] = '""'
    for name in re.findall(
        r'\b(\w+)\s*\.\s*length\s*\(\s*\)\s*(?:==|<=)\s*0', condition
    ):
        if name in param_names:
            hints[name] = '""'
    for name in re.findall(r'\b(\w+)\s*<\s*(?:0|1)\b', condition):
        if name in param_names:
            hints[name] = '-1'
    return hints


# ─── public observer lookup ────────────────────────────────────────────────

def _public_observer(field: str, class_name: str, inventory: dict) -> Optional[str]:
    """
    Finds a getter or query method that exposes *field* on *class_name*.
    Returns a Java call expression ``instance.getX()`` or None.
    """
    entry = _inventory_entry(class_name, inventory)
    if not entry:
        return None

    candidates = [
        f'get{field[0].upper()}{field[1:]}',
        f'is{field[0].upper()}{field[1:]}',
        f'has{field[0].upper()}{field[1:]}',
        'size', 'isEmpty', 'getCount', 'length', 'getLength',
    ]

    for sig in entry.get('public_methods', []):
        name_m = re.search(r'\b(\w+)\s*\(', sig)
        if not name_m:
            continue
        mname = name_m.group(1)
        for pat in candidates:
            if mname.lower() == pat.lower():
                return f'instance.{mname}()'
    return None


# ─── edge case detection ───────────────────────────────────────────────────

def _is_trivial_body(body: str) -> bool:
    """
    Returns True for method bodies that have no testable behavior:
    empty bodies, abstract stubs, and super()/this() delegation only.
    """
    if not body:
        return True
    stripped = body.strip().strip('{}').strip()
    if not stripped:
        return True
    # super(args); or this(args); — pure delegation, nothing to test here
    if re.fullmatch(r'(?:super|this)\s*\([^)]*\)\s*;', stripped):
        return True
    # Just a semicolon or only whitespace
    if re.fullmatch(r';?\s*', stripped):
        return True
    return False


# ─── observable builder for THROW_SLICE ───────────────────────────────────

def _throw_observable(method_name: str, params: list, recipe: dict,
                      exc_type: str, message: Optional[str]) -> str:
    """
    Builds the assertThrows + optional assertEquals(message) observable.
    """
    if params:
        call_args = ', '.join(recipe.get(n, 'null') for _, n in params)
        call = f'sut.{method_name}({call_args})'
    else:
        call = f'sut.{method_name}()'

    obs = f'assertThrows({exc_type}.class, () -> {call})'
    if message:
        obs += f'\nassertEquals("{message}", ex.getMessage())'
    return obs


# ─── slice extractors ──────────────────────────────────────────────────────

def _extract_throw_slices(body: str, params: list, method_name: str,
                           inventory: dict, ids) -> list:
    """Extracts THROW_SLICE for every ``throw new ExceptionType(...)``."""
    result = []
    seen_exc = set()

    for m in _THROW_RE.finditer(body):
        exc_type = m.group(1)
        ctor_args = m.group(2).strip()

        if exc_type in seen_exc:
            continue
        seen_exc.add(exc_type)

        # Extract message literal from constructor arguments
        msg_m = re.search(r'"((?:[^"\\]|\\.)*)"', ctor_args)
        message = msg_m.group(1) if msg_m else None

        condition = _nearest_if_condition(body, m.start())
        hints = _condition_hints(condition, params) if condition else {}
        recipe, _conf = _recipe(params, inventory, hints)

        observable = _throw_observable(method_name, params, recipe,
                                       exc_type, message)

        result.append({
            'slice_id':           next(ids),
            'slice_type':         'THROW_SLICE',
            'entry_condition':    f'({condition})' if condition else 'unconditional throw',
            'exception_type':     exc_type,
            'message_pattern':    message,
            'input_recipe':       recipe,
            'expected_observable': observable,
            'confidence':         'high' if condition else 'medium',
            'skip_reason':        None,
        })

    return result


def _extract_return_slices(body: str, params: list, method_name: str,
                            inventory: dict, ids) -> list:
    """
    Extracts RETURN_SLICE for:
      1. Literal returns (true/false/null/string/number) — confidence=high
      2. ``return new Type(...)`` returns — confidence=medium
    """
    result = []
    seen_keys: set = set()

    # 1. Literal returns
    for m in _RETURN_LITERAL_RE.finditer(body):
        value = m.group(1)
        key = f'lit:{value}'
        if key in seen_keys:
            continue
        seen_keys.add(key)

        condition = _nearest_if_condition(body, m.start())
        hints = _condition_hints(condition, params) if condition else {}
        recipe, _conf = _recipe(params, inventory, hints)

        observable = 'assertNull(result)' if value == 'null' else f'assertEquals({value}, result)'

        result.append({
            'slice_id':           next(ids),
            'slice_type':         'RETURN_SLICE',
            'entry_condition':    f'({condition})' if condition else _source_line(body, m.start()),
            'return_descriptor':  f'exact literal {value}',
            'input_recipe':       recipe,
            'expected_observable': observable,
            'confidence':         'high',
            'skip_reason':        None,
        })

    # 2. return new Type(...) — object construction returns
    for m in _RETURN_NEW_RE.finditer(body):
        type_name = m.group(1)
        key = f'new:{type_name}'
        if key in seen_keys:
            continue
        seen_keys.add(key)

        condition = _nearest_if_condition(body, m.start())
        hints = _condition_hints(condition, params) if condition else {}
        recipe, _conf = _recipe(params, inventory, hints)

        # Cannot statically derive exact field values — require LLM reasoning
        observable = (
            f'assertNotNull(result);  '
            f'// additionally: verify result.get*() fields match expected {type_name} state '
            f'(derive exact values from method body in Step 1)'
        )

        result.append({
            'slice_id':           next(ids),
            'slice_type':         'RETURN_SLICE',
            'entry_condition':    f'({condition})' if condition else _source_line(body, m.start()),
            'return_descriptor':  f'returns new {type_name}(...)',
            'input_recipe':       recipe,
            'expected_observable': observable,
            'confidence':         'medium',
            'skip_reason':        None,
        })

    return result


def _extract_mutation_slices(body: str, params: list, class_name: str,
                              inventory: dict, ids) -> list:
    """
    Extracts MUTATION_SLICE for every ``this.field = ...`` assignment.
    confidence=medium when a public observer exists; confidence=low otherwise.
    """
    result = []
    seen_fields: set = set()

    for m in _FIELD_ASSIGN_RE.finditer(body):
        field = m.group(1)
        if field in seen_fields:
            continue
        seen_fields.add(field)

        condition = _nearest_if_condition(body, m.start())
        hints = _condition_hints(condition, params) if condition else {}
        recipe, _conf = _recipe(params, inventory, hints)

        observer = _public_observer(field, class_name, inventory)

        if observer:
            observable = (
                f'assertEquals(expectedValue, {observer})  '
                f'// verify this.{field} was mutated as expected'
            )
            confidence = 'medium'
            skip_reason = None
        else:
            observable = (
                f'// SKIP: no public getter/observer found for this.{field}'
            )
            confidence = 'low'
            skip_reason = (
                f'No public observer (getter/query method) found for '
                f'mutated field this.{field} on {class_name}'
            )

        result.append({
            'slice_id':           next(ids),
            'slice_type':         'MUTATION_SLICE',
            'entry_condition':    (
                f'({condition})' if condition
                else f'unconditional mutation of this.{field}'
            ),
            'mutated_target':     f'this.{field}',
            'public_observer':    observer,
            'input_recipe':       recipe,
            'expected_observable': observable,
            'confidence':         confidence,
            'skip_reason':        skip_reason,
        })

    return result


def _extract_loop_slices(body: str, params: list, inventory: dict, ids) -> list:
    """
    Extracts LOOP_SLICE for the first loop in the body.
    Always produces two sub-slices: empty-input and single-element.
    """
    loop_matches = list(_LOOP_RE.finditer(body))
    if not loop_matches:
        return []

    m = loop_matches[0]
    entry_cond = _source_line(body, m.start())

    # Identify the collection parameter being iterated
    collection_param: Optional[tuple] = None

    # Check enhanced for-loops first
    for ef in _ENHANCED_FOR_RE.finditer(body):
        coll_name = ef.group(2).strip()
        for ptype, pname in params:
            if pname == coll_name:
                collection_param = (ptype, pname)
                break
        if collection_param:
            break

    # Fall back to any param whose type looks like a collection/array
    if not collection_param:
        for ptype, pname in params:
            clean = re.sub(r'<[^>]*>', '', ptype)
            if any(t in clean for t in
                   ('List', 'Collection', 'Iterable', 'Set', '[]', 'Array')):
                collection_param = (ptype, pname)
                break

    # Build recipes for empty and single-element inputs
    empty_hints: dict = {}
    single_hints: dict = {}
    if collection_param:
        ptype, pname = collection_param
        empty_hints[pname] = 'new java.util.ArrayList<>()'
        # Derive element type for the single-element list
        elem_m = re.search(r'<([^>]+)>', ptype)
        elem_type = elem_m.group(1).strip() if elem_m else 'Object'
        elem_simple = re.sub(r'<[^>]*>', '', elem_type.split('.')[-1]).strip()
        elem_expr, _ = _construction(elem_simple, inventory)
        single_hints[pname] = f'java.util.Collections.singletonList({elem_expr})'

    empty_recipe, _ = _recipe(params, inventory, empty_hints)
    single_recipe, _ = _recipe(params, inventory, single_hints)

    return [
        {
            'slice_id':           next(ids),
            'slice_type':         'LOOP_SLICE',
            'loop_variant':       'empty',
            'entry_condition':    (
                f'zero iterations — empty/null collection input '
                f'(loop: {entry_cond})'
            ),
            'input_recipe':       empty_recipe,
            'expected_observable': (
                'assertEquals(0, result.size())  '
                '// or equivalent zero-iteration assertion — '
                'derive exact check from method body'
            ),
            'confidence':         'medium',
            'skip_reason':        None,
        },
        {
            'slice_id':           next(ids),
            'slice_type':         'LOOP_SLICE',
            'loop_variant':       'single',
            'entry_condition':    (
                f'one iteration — single-element collection input '
                f'(loop: {entry_cond})'
            ),
            'input_recipe':       single_recipe,
            'expected_observable': (
                'assertEquals(1, result.size())  '
                '// or equivalent single-iteration assertion — '
                'derive exact check from method body'
            ),
            'confidence':         'medium',
            'skip_reason':        None,
        },
    ]


def _extract_branch_slices(body: str, params: list, inventory: dict, ids,
                            covered_conditions: set) -> list:
    """
    Extracts BRANCH_SLICE for if() conditions not already covered by
    THROW_SLICE or RETURN_SLICE guards.
    """
    result = []
    seen: set = set()

    for m in _IF_OPEN_RE.finditer(body):
        abs_open = m.end() - 1
        depth = 1
        i = abs_open + 1
        while i < len(body) and depth > 0:
            c = body[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            i += 1
        if depth != 0:
            continue
        condition = body[abs_open + 1: i - 1].strip()
        if not condition or condition in covered_conditions or condition in seen:
            continue
        seen.add(condition)

        hints = _condition_hints(condition, params)
        recipe, _conf = _recipe(params, inventory, hints)

        result.append({
            'slice_id':           next(ids),
            'slice_type':         'BRANCH_SLICE',
            'entry_condition':    f'branch taken when ({condition})',
            'input_recipe':       recipe,
            'expected_observable': (
                'verify the state or return value when this branch is taken '
                '(derive exact JUnit 5 assertion from method body in Step 1)'
            ),
            'confidence':         'medium',
            'skip_reason':        None,
        })

    return result


# ─── public API ────────────────────────────────────────────────────────────

def slice_method(
    method_body: str,
    method_metadata: dict,
    class_inventory: dict,
) -> list:
    """
    Produces a list of behavioral slice dicts for *method_body*.

    Args:
        method_body:      Raw Java method body string (braces optional).
        method_metadata:  Dict with keys: signature, class_name, full_name,
                          method_name, body, source_file_imports,
                          dependency_signatures.
        class_inventory:  Full class inventory dict (from context_loader).

    Returns:
        List of slice dicts.  Empty list for trivial / un-testable bodies.
    """
    if _is_trivial_body(method_body):
        return []

    sig = method_metadata.get('signature', '')
    class_name = method_metadata.get('class_name', '')
    method_name = method_metadata.get('method_name') or _method_name_from_sig(sig)
    params = _parse_params(sig)

    ids = _slice_ids()

    # Collect conditions already owned by throw slices so branch slices
    # do not create duplicate test cases.
    covered_conditions: set = set()

    throw_slices = _extract_throw_slices(
        method_body, params, method_name, class_inventory, ids
    )
    for s in throw_slices:
        cond_raw = s['entry_condition'].strip('()')
        if cond_raw not in ('unconditional throw',):
            covered_conditions.add(cond_raw)

    return_slices = _extract_return_slices(
        method_body, params, method_name, class_inventory, ids
    )
    for s in return_slices:
        cond_raw = s['entry_condition']
        if cond_raw.startswith('(') and cond_raw.endswith(')'):
            covered_conditions.add(cond_raw.strip('()'))

    mutation_slices = _extract_mutation_slices(
        method_body, params, class_name, class_inventory, ids
    )

    loop_slices = _extract_loop_slices(
        method_body, params, class_inventory, ids
    )

    # Add branch slices only when throw + return don't already cover the
    # method's main branching structure (avoids low-value duplicates).
    primary_slices = throw_slices + return_slices
    if len(primary_slices) <= 1:
        branch_slices = _extract_branch_slices(
            method_body, params, class_inventory, ids, covered_conditions
        )
    else:
        branch_slices = []

    return (
        throw_slices
        + return_slices
        + mutation_slices
        + loop_slices
        + branch_slices
    )


def get_testable_slices(slices: list) -> list:
    """Returns slices whose confidence is 'high' or 'medium'."""
    return [s for s in slices if s.get('confidence') in ('high', 'medium')]


def get_skip_report(slices: list) -> list:
    """Returns slices with confidence 'low', each containing skip_reason."""
    return [s for s in slices if s.get('confidence') == 'low']
