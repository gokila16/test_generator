import re

# JUnit 5 / standard assertion methods - always allowed when called on Assertions class or statically
_JUNIT_CLASSES = {'Assertions', 'Assert', 'Assume'}

# Common Java Object methods - always allowed on any receiver type
_JAVA_OBJECT_METHODS = {
    'equals', 'hashCode', 'toString', 'getClass', 'notify', 'notifyAll', 'wait',
}

_PARAM_MODIFIERS = frozenset({'final', 'volatile', 'transient'})


def _parse_param_types(signature: str) -> list:
    """
    Extracts parameter types from a full Java method signature string.
    Returns only the simple (unqualified) type name for each parameter.

    Examples:
        "public void save(File fileName) throws IOException" -> ["File"]
        "public boolean load(String name, int index)"       -> ["String", "int"]
        "public void close()"                               -> []
    """
    m = re.search(r'\(([^)]*)\)', signature)
    if not m:
        return []
    params_str = m.group(1).strip()
    if not params_str:
        return []

    # Split by comma, but respect angle-bracket depth (for generic types).
    raw_params = []
    depth = 0
    current = []
    for ch in params_str:
        if ch == '<':
            depth += 1
            current.append(ch)
        elif ch == '>':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            raw_params.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        raw_params.append(''.join(current).strip())

    types = []
    for param in raw_params:
        if not param:
            continue
        # Strip varargs marker
        param = param.replace('...', '').strip()
        # Remove the trailing variable name (last identifier token)
        type_part = re.sub(r'\s+\w+\s*$', '', param).strip()
        if not type_part:
            # No space between type and name — the whole token is the type
            type_part = param
        # Remove modifier keywords
        type_tokens = [t for t in type_part.split() if t not in _PARAM_MODIFIERS]
        if not type_tokens:
            continue
        # First remaining token is the type; strip package prefix and generics
        raw_type = type_tokens[0]
        simple = raw_type.split('.')[-1].split('<')[0]
        types.append(simple)
    return types


def _count_call_args(code: str, paren_start: int) -> int:
    """
    Counts the number of arguments in a method call starting just after '('.
    Tracks paren depth so nested calls do not inflate the count.
    Returns 0 for empty argument lists.
    """
    depth = 1
    i = paren_start
    commas = 0
    while i < len(code) and depth > 0:
        ch = code[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                break
        elif ch == ',' and depth == 1:
            commas += 1
        i += 1
    content = code[paren_start:i].strip()
    return 0 if not content else commas + 1


def _build_allowlist(method):
    """
    Builds a dict mapping (class_name, method_name) -> list[list[str]]
    from dependency_signatures, where each inner list is the parameter types
    for one overload.
    """
    deps = method.get('dependency_signatures', [])
    allowed = {}
    for d in deps:
        sig = d['signature']
        class_name = d['class_name']
        # Extract the method name — identifier immediately before '('
        m = re.search(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', sig)
        if m:
            method_name = m.group(1)
            # Skip constructors (same name as class)
            if method_name != class_name:
                key = (class_name, method_name)
                param_types = _parse_param_types(sig)
                if key not in allowed:
                    allowed[key] = []
                allowed[key].append(param_types)
    return allowed


def _build_type_map(java_code):
    """
    Parses variable declarations in Java code to map variable name -> class name.
    Handles: `TypeName varName = ...;` and `TypeName varName;`
    Strips generic type parameters (e.g. List<String> -> List).
    Only considers declarations where the type starts with an uppercase letter
    (i.e. a class name, not a primitive).
    """
    type_map = {}
    # Match: TypeName varName followed by = ; ( or ,
    # Type must start with uppercase (class), var must start with lowercase
    pattern = re.compile(
        r'\b([A-Z][A-Za-z0-9_]*(?:<[^>]*>)?)\s+([a-z][A-Za-z0-9_]*)\s*[=;(,]'
    )
    for match in pattern.finditer(java_code):
        class_name = match.group(1).split('<')[0]  # strip generics
        var_name = match.group(2)
        type_map[var_name] = class_name
    return type_map


def _get_dep_class_names(method):
    """Returns the set of class names present in dependency_signatures."""
    return {d['class_name'] for d in method.get('dependency_signatures', [])}


def _build_return_type_map(method):
    """
    Builds a map of (class_name, method_name) -> return_class_name from
    dependency_signatures. Only captures return types that are class names
    (start with uppercase). Skips void, primitives, and arrays.
    """
    return_type_map = {}
    deps = method.get('dependency_signatures', [])
    for d in deps:
        sig = d['signature']
        class_name = d['class_name']
        # Extract return type: the token before the method name
        # Signature form: [modifiers] ReturnType methodName(...)
        # We strip modifiers (public/protected/static/final/synchronized) then take
        # the next token as the return type and the one after as the method name.
        stripped = re.sub(
            r'\b(public|protected|private|static|final|synchronized|abstract|native)\b', '', sig
        ).strip()
        # Now: "ReturnType methodName(...)"
        m = re.match(r'([A-Za-z_][A-Za-z0-9_<>\[\],\s]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', stripped)
        if not m:
            continue
        return_type_raw = m.group(1).strip().split('<')[0]  # strip generics
        method_name = m.group(2)
        # Only track class return types (uppercase start), skip void/primitives
        if not return_type_raw or not return_type_raw[0].isupper():
            continue
        # Skip constructors
        if method_name == class_name:
            continue
        return_type_map[(class_name, method_name)] = return_type_raw
    return return_type_map


def _enrich_type_map(java_code, type_map, return_type_map):
    """
    Second pass: infers variable types from the return types of method calls.
    For each assignment `varName = receiver.methodName(`, if receiver's type is
    already known and (receiver_type, methodName) is in return_type_map, adds
    varName -> return_type to type_map.
    Mutates type_map in place.
    """
    # Match: varName = receiver.methodName(
    assign_pattern = re.compile(
        r'\b([a-z][A-Za-z0-9_]*)\s*=\s*([a-z][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\('
    )
    for match in assign_pattern.finditer(java_code):
        lhs_var = match.group(1)
        receiver = match.group(2)
        called_method = match.group(3)
        receiver_type = type_map.get(receiver)
        if receiver_type is None:
            continue
        inferred_type = return_type_map.get((receiver_type, called_method))
        if inferred_type and lhs_var not in type_map:
            type_map[lhs_var] = inferred_type


def _build_inventory_class_names(method):
    """
    Returns the set of simple class names extracted from source_file_imports.
    """
    import re
    imports = method.get('source_file_imports', [])
    names = set()
    pattern = re.compile(r'import\s+[\w.]*\.([A-Z][A-Za-z0-9_]*)\s*;')
    for imp in imports:
        m = pattern.search(imp)
        if m:
            names.add(m.group(1))
    return names


def _build_inventory_method_allowlist(method, class_inventory):
    """
    Builds a set of (class_name, method_name) pairs from the public_methods
    list in class_inventory for every class in source_file_imports.

    Used to validate calls on inventory classes — the LLM can call any method
    listed here, but hallucinated names that don't exist in public_methods
    are flagged.

    Returns an empty dict if class_inventory is not provided.
    """
    if not class_inventory:
        return {}

    import re
    import_pattern = re.compile(r'import\s+([\w.]+);')
    method_name_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')

    allowed = {}   # class_name (simple) -> set of method names
    for imp in method.get('source_file_imports', []):
        m = import_pattern.search(imp)
        if not m:
            continue
        full_name = m.group(1)
        entry = class_inventory.get(full_name)
        if not entry:
            continue
        simple = entry.get('class_name', full_name.split('.')[-1])
        method_names = set()
        for sig in entry.get('public_methods', []):
            mn = method_name_pattern.search(sig)
            if mn:
                method_names.add(mn.group(1))
        # Also allow factory method names
        for fm in entry.get('factory_methods', []):
            method_names.add(fm['name'])
        allowed[simple] = method_names
    return allowed


def check_against_allowlist(java_code, method, class_inventory=None):
    """
    Checks that the generated test only calls methods that exist in
    dependency_signatures or in public_methods from class_inventory.

    Strategy:
    - Build a type map from variable declarations (VarName -> ClassName).
    - For every `receiver.methodName(` call found in the code:
        - If receiver is a JUnit assertions class -> skip (always allowed).
        - If methodName is a universal Java Object method -> skip.
        - If receiver maps to a class in dep_classes:
            -> check (ClassName, methodName) against the dep allowlist.
        - If receiver maps to a class in inventory_classes:
            -> if class_inventory provided, check against public_methods list.
            -> if class_inventory not provided, skip (no false positive).
        - If receiver starts with uppercase (static call) and is in dep_classes:
            -> check (ReceiverAsClass, methodName) against the dep allowlist.
        - Otherwise -> cannot determine type, skip (no false positive).

    Returns:
        (passed: bool, violations: list[str])
        violations: "ClassName.methodName" strings called but not in allowlist.
    """
    allowlist = _build_allowlist(method)
    type_map = _build_type_map(java_code)
    return_type_map = _build_return_type_map(method)
    _enrich_type_map(java_code, type_map, return_type_map)
    dep_classes = _get_dep_class_names(method)
    inventory_classes = _build_inventory_class_names(method)
    inventory_method_allowlist = _build_inventory_method_allowlist(method, class_inventory)

    violations = []
    seen = set()

    call_pattern = re.compile(
        r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    )

    for match in call_pattern.finditer(java_code):
        receiver = match.group(1)
        called_method = match.group(2)

        # Always skip JUnit assertion/assumption classes
        if receiver in _JUNIT_CLASSES:
            continue

        # Always skip universal Java Object methods
        if called_method in _JAVA_OBJECT_METHODS:
            continue

        # Skip 'this' and 'super' receivers
        if receiver in ('this', 'super'):
            continue

        # Resolve receiver to a class name
        if receiver[0].islower():
            # Instance variable — look up in type map
            resolved_class = type_map.get(receiver)
            if resolved_class is None:
                # Type unknown — cannot verify, skip to avoid false positives
                continue
            if resolved_class in inventory_classes and resolved_class not in dep_classes:
                # Inventory class: validate against public_methods if available
                if resolved_class in inventory_method_allowlist:
                    if called_method not in inventory_method_allowlist[resolved_class]:
                        qualified = f"{resolved_class}.{called_method}"
                        if qualified not in seen:
                            seen.add(qualified)
                            violations.append(qualified)
                # If no public_methods data, skip (no false positive)
                continue
            if resolved_class not in dep_classes:
                continue
        else:
            # Uppercase receiver — treat as a static/class-level call
            if receiver in inventory_classes and receiver not in dep_classes:
                if receiver in inventory_method_allowlist:
                    if called_method not in inventory_method_allowlist[receiver]:
                        qualified = f"{receiver}.{called_method}"
                        if qualified not in seen:
                            seen.add(qualified)
                            violations.append(qualified)
                continue
            if receiver not in dep_classes:
                continue
            resolved_class = receiver

        key = (resolved_class, called_method)
        qualified = f"{resolved_class}.{called_method}"
        if key not in allowlist:
            # HALLUCINATED_METHOD: the method does not exist in dependency_signatures
            if qualified not in seen:
                seen.add(qualified)
                violations.append(qualified)
        else:
            # Method exists — check that at least one overload matches the call arity
            known_overloads = allowlist[key]
            known_arities = [len(ol) for ol in known_overloads]
            call_arg_count = _count_call_args(java_code, match.end())
            if call_arg_count not in known_arities:
                if qualified not in seen:
                    seen.add(qualified)
                    overloads_encoded = '||'.join(','.join(ol) for ol in known_overloads)
                    violations.append(
                        f"TYPE_MISMATCH::{qualified}::{call_arg_count}::{overloads_encoded}"
                    )

    if violations:
        print("  [ALLOWLIST DEBUG] Violations found:")
        for v in violations:
            print(f"    VIOLATION: {v}")
        print("  [ALLOWLIST DEBUG] Full allowlist (dep_classes x methods):")
        by_class = {}
        for (cls, mth) in allowlist:
            by_class.setdefault(cls, []).append(mth)
        for cls in sorted(by_class):
            print(f"    {cls}: {sorted(by_class[cls])}")
        print("  [ALLOWLIST DEBUG] Type map resolved:")
        for var, cls in sorted(type_map.items()):
            marker = " [dep]" if cls in dep_classes else " [java]"
            print(f"    {var} -> {cls}{marker}")

    return len(violations) == 0, violations
