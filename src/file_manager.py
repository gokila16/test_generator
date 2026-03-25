import os
import shutil

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
    # Remove last two parts (class name and method name)
    package_parts = parts[:-2]
    return '.'.join(package_parts)

def get_test_class_name(class_name, method_name, index=None):
    if index is not None:
        return f"{class_name}_{method_name}_{index}_Test"
    return f"{class_name}Test"

def save_prompt(prompts_dir, full_name, prompt, is_retry=False):
    """Saves prompt to prompts/ folder"""
    os.makedirs(prompts_dir, exist_ok=True)
    suffix = '_retry_prompt.txt' if is_retry else '_prompt.txt'
    path = os.path.join(prompts_dir, sanitize_name(full_name) + suffix)
    with open(path, 'w') as f:
        f.write(prompt)
    return path

def save_response(responses_dir, full_name, response, is_retry=False):
    """Saves raw LLM response to responses/ folder"""
    os.makedirs(responses_dir, exist_ok=True)
    suffix = '_retry_response.txt' if is_retry else '_response.txt'
    path = os.path.join(responses_dir, sanitize_name(full_name) + suffix)
    with open(path, 'w') as f:
        f.write(response or '')
    return path

def save_test_file(generated_tests_dir, class_name, java_code):
    """Saves generated Java test file"""
    os.makedirs(generated_tests_dir, exist_ok=True)
    test_class = get_test_class_name(class_name)
    path = os.path.join(generated_tests_dir, f"{test_class}.java")
    with open(path, 'w') as f:
        f.write(java_code)
    return path
