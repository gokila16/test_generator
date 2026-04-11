import re


# Matches: URL <varName> = <expr ending with getResource(...)>;
# followed immediately by: <Type> <varName2> = new <Type>(<varName>.toURI());
_URL_VAR_PATTERN = re.compile(
    r'URL\s+(\w+)\s*=\s*(.+?getResource\([^)]*\))\s*;\s*\n'
    r'(\s*)(\w+)\s+(\w+)\s*=\s*new\s+\4\s*\(\s*\1\s*\.\s*toURI\s*\(\s*\)\s*\)\s*;',
    re.MULTILINE
)


def _collapse_url_variable(java_code):
    """
    Collapses two-line URL variable pattern into a single line:

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

    # Insert after the last existing import line
    last_import = None
    for m in re.finditer(r'^import\s+[\w.]+;', java_code, re.MULTILINE):
        last_import = m
    if last_import:
        insert_at = last_import.end()
        return java_code[:insert_at] + '\nimport java.net.URL;' + java_code[insert_at:]

    # No imports at all — insert before the first class declaration
    m = re.search(r'^(public\s+|abstract\s+|final\s+)*class\s+', java_code, re.MULTILINE)
    if m:
        return java_code[:m.start()] + 'import java.net.URL;\n' + java_code[m.start():]

    return java_code


def _add_throws_exception(java_code):
    """
    Ensures every @Test method declares 'throws Exception'.
    Handles methods that already declare some throws clause or none at all.
    """
    # Match @Test annotation followed (possibly by other annotations) by the method signature
    # Handles: @Test\n  [modifiers] void methodName() {
    #          @Test\n  [modifiers] void methodName() throws SomeException {
    pattern = re.compile(
        r'(@Test\s+(?:@\w+(?:\([^)]*\))?\s+)*)'   # @Test and any other annotations
        r'((?:public\s+|protected\s+)?void\s+\w+\s*\([^)]*\))'  # method signature
        r'(\s*)\{',                                               # opening brace
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


def post_process_java(java_code):
    """
    Applies all post-processing fixes to generated Java code in order:
    1. Collapse two-line URL variable pattern into single-line getResource call.
    2. Fallback: inject 'import java.net.URL;' if URL variable type remains.
    3. Add 'throws Exception' to @Test methods that are missing it.

    Returns the fixed Java code string.
    """
    if not java_code:
        return java_code
    java_code = _collapse_url_variable(java_code)
    java_code = _ensure_url_import(java_code)
    java_code = _add_throws_exception(java_code)
    return java_code
