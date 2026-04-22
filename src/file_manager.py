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

def save_prompt(prompts_dir, full_name, prompt, is_retry=False, is_allowlist=False, is_plan=False, is_recovery=False):
    """Saves prompt to prompts/ folder"""
    os.makedirs(prompts_dir, exist_ok=True)
    if is_plan:
        suffix = '_plan_prompt.txt'
    elif is_recovery:
        suffix = '_recovery_prompt.txt'
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

def save_response(responses_dir, full_name, response, is_retry=False, is_allowlist=False):
    """Saves raw LLM response to responses/ folder"""
    os.makedirs(responses_dir, exist_ok=True)
    if is_allowlist:
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