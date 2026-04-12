import os
import shutil
import config

def sanitize_name(full_name):
    """Converts full method name to safe filename"""
    return full_name.replace('.', '_').replace('/', '_')

def get_package_from_full_name(full_name):
    """
    Extracts package from full method name
    org.apache.pdfbox.multipdf.PDFMergerUtility.appendDocument
    -> org.apache.pdfbox.multipdf
    """
    parts = full_name.split('.')
    package_parts = parts[:-2]
    return '.'.join(package_parts)

def get_test_class_name(class_name, method_name, index=None):
    if index is not None:
        return f"{class_name}_{method_name}_{index}_Test"
    return f"{class_name}_{method_name}_Test"

def get_test_destination(full_name, class_name, method_name, overload_index=None):
    """
    Gets destination path in src/test/java matching package structure
    e.g. org.apache.pdfbox.multipdf.PDFMergerUtility.appendDocument
    -> src/test/java/org/apache/pdfbox/multipdf/PDFMergerUtility_appendDocument_Test.java
    """
    parts = full_name.split('.')
    package_parts = parts[:-2]  # remove class name and method name
    package_path = os.path.join(*package_parts) if package_parts else ''

    if package_path:
        dest_dir = os.path.join(
            config.PDFBOX_DIR,
            'src', 'test', 'java',
            package_path
        )
    else:
        dest_dir = os.path.join(config.PDFBOX_DIR, 'src', 'test', 'java')
    dest_dir = os.path.normpath(dest_dir)
    test_class = get_test_class_name(class_name, method_name, overload_index)
    return dest_dir, f"{test_class}.java"

def save_prompt(prompts_dir, full_name, prompt, is_retry=False, is_allowlist=False,
               is_plan=False, prompt_type=None):
    """
    Saves prompt to prompts/ folder.
    prompt_type (str): when provided, used directly as suffix label, e.g.
        'checklist'         -> _checklist_prompt.txt
        'checklist_fix'     -> _checklist_fix_prompt.txt
        'resource'          -> _resource_prompt.txt
        'resource_fix'      -> _resource_fix_prompt.txt
    Falls back to the existing bool flags when prompt_type is None.
    """
    os.makedirs(prompts_dir, exist_ok=True)
    if prompt_type is not None:
        suffix = f'_{prompt_type}_prompt.txt'
    elif is_plan:
        suffix = '_plan_prompt.txt'
    elif is_allowlist:
        suffix = '_allowlist_prompt.txt'
    elif is_retry:
        suffix = '_retry_prompt.txt'
    else:
        suffix = '_gen_prompt.txt'
    path = os.path.join(prompts_dir, sanitize_name(full_name) + suffix)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(prompt)
    return path

def save_response(responses_dir, full_name, response, is_retry=False, is_allowlist=False,
                  response_type=None):
    """
    Saves raw LLM response to responses/ folder.
    response_type (str): when provided, used directly as suffix label, e.g.
        'checklist'         -> _checklist_response.txt
        'checklist_fix'     -> _checklist_fix_response.txt
        'resource'          -> _resource_response.txt
        'resource_fix'      -> _resource_fix_response.txt
    Falls back to the existing bool flags when response_type is None.
    """
    os.makedirs(responses_dir, exist_ok=True)
    if response_type is not None:
        suffix = f'_{response_type}_response.txt'
    elif is_allowlist:
        suffix = '_allowlist_response.txt'
    elif is_retry:
        suffix = '_retry_response.txt'
    else:
        suffix = '_response.txt'
    path = os.path.join(responses_dir, sanitize_name(full_name) + suffix)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(response or '')
    return path

def save_plan(plans_dir, full_name, plan):
    """Saves the LLM-generated test plan to plans/ folder"""
    os.makedirs(plans_dir, exist_ok=True)
    path = os.path.join(plans_dir, sanitize_name(full_name) + '_plan.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(plan or '')
    return path

def assemble_test_package(test_code: str, resources: dict, method: dict,
                          overload_index=None) -> str:
    """
    Injects pre-built resource construction statements into the test class.

    Strategy:
      Case A — @BeforeEach exists:
        Inject all resource declarations at the end of the @BeforeEach body,
        skipping any variable that is already declared there.

      Case B — no @BeforeEach:
        For each @Test method, find which resource variable names are
        referenced inside that method and inject only those declarations
        at the top of that method body, skipping already-present variables.

    If the generated code already contains a declaration for a variable
    (detected by 'varName ='), that variable is not re-injected.

    Returns the assembled Java code string (unchanged if resources is empty).
    """
    import re

    if not resources:
        return test_code

    def _already_declared(code_fragment: str, var_name: str) -> bool:
        """True if varName = appears as an assignment in the fragment."""
        return bool(re.search(r'\b' + re.escape(var_name) + r'\s*=', code_fragment))

    def _find_method_close(code: str, body_start: int) -> int:
        """Returns the index of the matching closing brace for a method body."""
        depth = 1
        pos   = body_start
        while pos < len(code) and depth > 0:
            if code[pos] == '{':
                depth += 1
            elif code[pos] == '}':
                depth -= 1
            pos += 1
        return pos - 1   # index of the closing '}'

    def _indent_of(code: str, brace_pos: int) -> str:
        """Returns the indentation of the line that contains brace_pos."""
        line_start = code.rfind('\n', 0, brace_pos) + 1
        line       = code[line_start:brace_pos]
        m = re.match(r'^(\s*)', line)
        return m.group(1) if m else '    '

    # ---- Case A: @BeforeEach exists ----
    before_each_pat = re.compile(
        r'@BeforeEach\s+(?:public\s+|protected\s+)?void\s+\w+\s*\([^)]*\)'
        r'(?:\s+throws\s+[\w,\s]+)?\s*\{',
        re.MULTILINE,
    )
    be_match = before_each_pat.search(test_code)
    if be_match:
        body_start  = be_match.end()     # character after '{'
        close_pos   = _find_method_close(test_code, body_start)
        body_text   = test_code[body_start:close_pos]
        indent      = _indent_of(test_code, close_pos) + '    '

        lines_to_inject = []
        for var_name, res in resources.items():
            if not _already_declared(body_text, var_name):
                lines_to_inject.append(f"{indent}{res['construction']}")

        if lines_to_inject:
            injection = '\n' + '\n'.join(lines_to_inject) + '\n'
            test_code = test_code[:close_pos] + injection + test_code[close_pos:]
        return test_code

    # ---- Case B: no @BeforeEach — inject per @Test method ----
    test_method_pat = re.compile(
        r'@Test\s+(?:@\w+(?:\([^)]*\))?\s+)*'
        r'(?:public\s+|protected\s+)?void\s+\w+\s*\([^)]*\)'
        r'(?:\s+throws\s+[\w,\s]+)?\s*\{',
        re.MULTILINE,
    )

    matches = list(test_method_pat.finditer(test_code))
    # Process back-to-front so earlier insertions don't shift later positions
    for match in reversed(matches):
        body_start = match.end()
        close_pos  = _find_method_close(test_code, body_start)
        body_text  = test_code[body_start:close_pos]
        indent     = _indent_of(test_code, close_pos) + '    '

        lines_to_inject = []
        for var_name, res in resources.items():
            referenced = bool(re.search(r'\b' + re.escape(var_name) + r'\b', body_text))
            if referenced and not _already_declared(body_text, var_name):
                lines_to_inject.append(f"{indent}{res['construction']}")

        if lines_to_inject:
            # Inject at the very top of the method body (after the opening brace)
            injection = '\n' + '\n'.join(lines_to_inject) + '\n'
            test_code = test_code[:body_start] + injection + test_code[body_start:]

    return test_code


def save_test_file(generated_tests_dir, full_name, class_name, method_name, java_code, overload_index=None):
    """Saves generated Java test file organized into package subdirectories"""
    parts = full_name.split('.')
    package_parts = parts[:-2]  # remove class name and method name
    package_path = os.path.join(*package_parts) if package_parts else ''
    dest_dir = os.path.join(generated_tests_dir, package_path) if package_path else generated_tests_dir
    os.makedirs(dest_dir, exist_ok=True)
    test_class = get_test_class_name(class_name, method_name, overload_index)
    path = os.path.normpath(os.path.join(dest_dir, f"{test_class}.java"))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(java_code)
    return path