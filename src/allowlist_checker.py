import re

# JUnit 5 / standard assertion methods - always allowed when called on Assertions class or statically
_JUNIT_CLASSES = {'Assertions', 'Assert', 'Assume'}

# Common Java Object methods - always allowed on any receiver type
_JAVA_OBJECT_METHODS = {
    'equals', 'hashCode', 'toString', 'getClass', 'notify', 'notifyAll', 'wait',
}


def _build_allowlist(method):
    """
    Builds a set of (class_name, method_name) pairs from dependency_signatures.
    Also includes a set of allowed class names for cross-referencing.
    """
    deps = method.get('dependency_signatures', [])
    allowed = set()
    for d in deps:
        sig = d['signature']
        class_name = d['class_name']
        # Extract the method name — identifier immediately before '('
        m = re.search(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', sig)
        if m:
            method_name = m.group(1)
            # Skip constructors (same name as class)
            if method_name != class_name:
                allowed.add((class_name, method_name))
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


def check_against_allowlist(java_code, method):
    """
    Checks that the generated test only calls methods that exist in
    dependency_signatures, using class-qualified resolution where possible.

    Strategy:
    - Build a type map from variable declarations (VarName -> ClassName).
    - For every `receiver.methodName(` call found in the code:
        - If receiver is a JUnit assertions class -> skip (always allowed).
        - If methodName is a universal Java Object method -> skip.
        - If receiver maps to a known class via type_map:
            -> check (ClassName, methodName) against the allowlist.
        - If receiver starts with uppercase (likely a static/class-level call)
          and that class is in dependency_signatures:
            -> check (ReceiverAsClass, methodName) against the allowlist.
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
            # Only validate calls on dependency classes.
            # Standard Java classes (File, OutputStream, etc.) are not in
            # dep_classes and their methods should never be flagged.
            if resolved_class not in dep_classes:
                continue
        else:
            # Uppercase receiver — treat as a static/class-level call
            # Only check if this class appears in dependency_signatures
            if receiver not in dep_classes:
                continue
            resolved_class = receiver

        key = (resolved_class, called_method)
        if key not in allowlist:
            qualified = f"{resolved_class}.{called_method}"
            if qualified not in seen:
                seen.add(qualified)
                violations.append(qualified)

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
