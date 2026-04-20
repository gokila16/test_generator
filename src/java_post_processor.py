import re


def _check_class_redefinition(java_code, test_class_name):
    """
    Returns a list of class/interface/enum names defined at the TOP LEVEL of
    the file that are neither the expected test class nor the file's public class.

    Uses ^ (no leading whitespace) so only zero-indented declarations are
    matched.  Inner classes inside the test class body are always indented at
    least one level and are therefore never flagged as false positives.

    The public class is always the test class (even if its name doesn't exactly
    match test_class_name due to overload-index suffixing).  Name mismatches on
    the public class are the responsibility of _check_test_class_name, not this
    function — keeping the two checks from producing duplicate rejections.
    """
    # Find the one public class in the file — that is always the test class.
    public_match = re.search(r'^public\s+class\s+(\w+)', java_code, re.MULTILINE)
    public_class_name = public_match.group(1) if public_match else None

    pattern = re.compile(
        r'^(?:(?:public|protected|private|abstract|final|static|strictfp)\s+)*'
        r'(?:class|interface|enum|@interface)\s+([A-Za-z_][A-Za-z0-9_]*)',
        re.MULTILINE
    )
    extra = []
    for m in pattern.finditer(java_code):
        name = m.group(1)
        if name == test_class_name:
            continue  # expected test class name — fine
        if name == public_class_name:
            continue  # main class regardless of name — _check_test_class_name handles mismatches
        extra.append(name)
    return extra


def _is_sut_missing(java_code, sut_class_name):
    """
    Returns True if the SUT class name is ABSENT from the file body.

    Strips import/package lines before searching so a bare import of the class
    does not count as 'present' — it must be used in actual test code.
    """
    body = re.sub(r'^\s*(package|import)\s+[\w.*]+\s*;', '', java_code, flags=re.MULTILINE)
    return not bool(re.search(rf'\b{re.escape(sut_class_name)}\b', body))


def _check_mockito(java_code):
    """
    Returns True if the code imports Mockito (which is forbidden).
    """
    return bool(re.search(r'^\s*import\s+org\.mockito', java_code, re.MULTILINE))


def _is_truncated(java_code):
    """
    Returns True if the file appears to be cut off mid-generation.
    A well-formed Java file must end with a closing brace.
    """
    return not java_code.rstrip().endswith('}')


def _has_only_trivial_assertions(java_code):
    """
    Returns True if the file contains @Test methods but ALL of them are
    trivially passing (assertTrue(true) with no other assert, or empty body).
    """
    test_body_pattern = re.compile(
        r'@Test\s+(?:@\w+(?:\([^)]*\))?\s+)*'
        r'(?:public\s+|protected\s+)?void\s+\w+\s*\([^)]*\)'
        r'(?:\s+throws\s+[\w,\s]+)?\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}',
        re.MULTILINE | re.DOTALL
    )
    bodies = test_body_pattern.findall(java_code)
    if not bodies:
        return False  # no @Test methods — handled by _has_test_methods

    def _is_trivial(body):
        stripped = body.strip()
        if not stripped:
            return True
        no_comments = re.sub(r'//[^\n]*', '', stripped).strip()
        no_comments = re.sub(r'/\*.*?\*/', '', no_comments, flags=re.DOTALL).strip()
        if not no_comments:
            return True
        return bool(re.fullmatch(r'assertTrue\s*\(\s*true\s*\)\s*;', no_comments))

    return all(_is_trivial(b) for b in bodies)


def _has_test_methods(java_code):
    """
    Returns True if the file contains at least one @Test annotation outside
    of a comment. A file with no @Test methods compiles but produces no test
    results — a silent failure that should be caught before Maven runs.
    """
    # Strip single-line comments so // @Test does not count as a test method
    code_no_comments = re.sub(r'//[^\n]*', '', java_code)
    return bool(re.search(r'@Test\b', code_no_comments))


def _check_test_class_name(java_code, expected_test_class_name):
    """
    Verifies the public class name in the file matches expected_test_class_name.
    A mismatch causes a Java compile error (file name vs. class name).

    Returns:
        (True,  None)        — class name matches
        (False, actual_name) — mismatch; actual_name is what was found
        (False, None)        — no public class declaration found at all
    """
    m = re.search(r'^public\s+class\s+(\w+)', java_code, re.MULTILINE)
    if not m:
        return False, None
    actual_name = m.group(1)
    if actual_name == expected_test_class_name:
        return True, None
    return False, actual_name


# Matches:
#   URL <varName> = <expr ending with getResource(...)>;
#   [optional blank line]
#   <Type> <varName2> = new <Type>(<varName>.toURI());
_URL_VAR_PATTERN = re.compile(
    r'URL\s+(\w+)\s*=\s*(.+?getResource\([^)]*\))\s*;\s*\n\s*\n?'
    r'(\s*)(\w+)\s+(\w+)\s*=\s*new\s+\4\s*\(\s*\1\s*\.\s*toURI\s*\(\s*\)\s*\)\s*;',
    re.MULTILINE
)


def _collapse_url_variable(java_code):
    """
    Collapses two-line URL variable pattern into a single line.
    Handles an optional blank line between the two statements.

    Before:
        URL url = getClass().getClassLoader().getResource("file.pdf");
        File file = new File(url.toURI());

    After:
        File file = new File(getClass().getClassLoader().getResource("file.pdf").toURI());
    """
    def _replace(m):
        get_resource_expr = m.group(2).strip()
        indent            = m.group(3)
        type_name         = m.group(4)
        var_name          = m.group(5)
        return f"{indent}{type_name} {var_name} = new {type_name}({get_resource_expr}.toURI());"

    return _URL_VAR_PATTERN.sub(_replace, java_code)


def _ensure_url_import(java_code):
    """
    Fallback: if 'URL ' still appears as a variable type after the regex,
    inject 'import java.net.URL;' if it is not already present.
    """
    if 'URL ' not in java_code:
        return java_code
    if 'import java.net.URL;' in java_code:
        return java_code

    last_import = None
    for m in re.finditer(r'^import\s+[\w.]+;', java_code, re.MULTILINE):
        last_import = m
    if last_import:
        insert_at = last_import.end()
        return java_code[:insert_at] + '\nimport java.net.URL;' + java_code[insert_at:]

    m = re.search(r'^(public\s+|abstract\s+|final\s+)*class\s+', java_code, re.MULTILINE)
    if m:
        return java_code[:m.start()] + 'import java.net.URL;\n' + java_code[m.start():]

    return java_code


def _add_throws_exception(java_code):
    """
    Ensures every @Test method declares 'throws Exception'.
    """
    pattern = re.compile(
        r'(@Test\s+(?:@\w+(?:\([^)]*\))?\s+)*)'
        r'((?:public\s+|protected\s+)?void\s+\w+\s*\([^)]*\))'
        r'(\s*)\{',
        re.MULTILINE
    )

    def _replacer(m):
        annotations = m.group(1)
        signature   = m.group(2)
        space       = m.group(3)
        if 'throws' not in signature:
            return f"{annotations}{signature} throws Exception{space}{{"
        return m.group(0)

    return pattern.sub(_replacer, java_code)


def _ensure_package(java_code, expected_package):
    """
    Injects the correct package declaration if it is absent.
    Uses re.search with MULTILINE so a leading comment before the package
    declaration is not mistaken for a missing package.
    """
    if not expected_package:
        return java_code
    has_package = bool(re.search(r'^package\s+[\w.]+\s*;', java_code, re.MULTILINE))
    if has_package:
        return java_code
    return f"package {expected_package};\n\n" + java_code


def post_process_java(java_code, expected_package=None, test_class_name=None, sut_class_name=None):
    """
    Applies all post-processing fixes and validation to generated Java code.

    Fixes applied (in order):
    1. Collapse two-line URL variable pattern into single-line getResource call.
    2. Fallback: inject 'import java.net.URL;' if URL variable type remains.
    3. Add 'throws Exception' to @Test methods that are missing it.
    4. Inject package declaration if absent.

    Validation (returns None to signal rejection):
    - SUT class absent from code body — wrong class tested        (_is_sut_missing)
    - Extra top-level class/interface/enum definitions found       (_check_class_redefinition)
    - Public class name does not match expected test class name    (_check_test_class_name)
    - No @Test methods present — silent compile-pass, no results  (_has_test_methods)
    - Mockito import detected                                      (_check_mockito)
    - File appears truncated — does not end with '}'              (_is_truncated)
    - All @Test methods are trivial placeholders                   (_has_only_trivial_assertions)

    Returns the fixed Java code string, or None if the file should be rejected.
    """
    if not java_code:
        return java_code

    # --- Fixes ---
    java_code = _collapse_url_variable(java_code)
    java_code = _ensure_url_import(java_code)
    java_code = _add_throws_exception(java_code)
    if expected_package is not None:
        java_code = _ensure_package(java_code, expected_package)

    # --- Validation ---
    if sut_class_name and _is_sut_missing(java_code, sut_class_name):
        print(f"  [POST-PROCESS] Rejected: SUT class '{sut_class_name}' never appears in code body.")
        return None

    if test_class_name:
        extra_classes = _check_class_redefinition(java_code, test_class_name)
        if extra_classes:
            print(f"  [POST-PROCESS] Rejected: file redefines production class(es): {extra_classes}")
            return None

    if not _has_test_methods(java_code):
        print("  [POST-PROCESS] Rejected: no @Test methods found.")
        return None

    if _check_mockito(java_code):
        print("  [POST-PROCESS] Rejected: Mockito import detected.")
        return None

    if _is_truncated(java_code):
        print("  [POST-PROCESS] Rejected: file appears truncated (does not end with '}').")
        return None

    if _has_only_trivial_assertions(java_code):
        print("  [POST-PROCESS] Rejected: all @Test methods are trivial placeholders.")
        return None

    return java_code
