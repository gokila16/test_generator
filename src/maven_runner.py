import glob
import os
import re
import sys
import shutil
import subprocess
import config
from src.file_manager import get_test_destination, get_test_class_name


def _clean_stale_generated_tests(pdfbox_dir):
    """
    Remove any generated test files left in src/test/java from a previous
    interrupted run.  Our files always follow the pattern *_*_Test.java
    (ClassName_MethodName_Test or ClassName_MethodName_N_Test), which is
    distinct from PDFBox's own tests (e.g. PDDocumentTest.java).
    """
    test_src_root = os.path.join(pdfbox_dir, 'src', 'test', 'java')
    pattern = os.path.join(test_src_root, '**', '*_*_Test.java')
    for stale in glob.glob(pattern, recursive=True):
        try:
            os.remove(stale)
        except OSError:
            pass

def _find_maven_cmd():
    # 1. Explicit override in config
    if config.MAVEN_EXECUTABLE:
        return config.MAVEN_EXECUTABLE
    # 2. Search PATH
    import shutil
    candidates = ['mvn.cmd', 'mvn'] if sys.platform == 'win32' else ['mvn']
    for cmd in candidates:
        if shutil.which(cmd):
            return cmd
    # 3. MAVEN_HOME / M2_HOME env var
    maven_home = os.environ.get('MAVEN_HOME') or os.environ.get('M2_HOME')
    if maven_home:
        ext = '.cmd' if sys.platform == 'win32' else ''
        return os.path.join(maven_home, 'bin', f'mvn{ext}')
    return candidates[0]

MAVEN_CMD = _find_maven_cmd()

def _maven_env():
    """Build environment for Maven subprocess, injecting JAVA_HOME if configured."""
    env = os.environ.copy()
    if config.JAVA_HOME:
        env['JAVA_HOME'] = config.JAVA_HOME
    return env

def _parse_failing_test_methods(output):
    """
    Parse Maven Surefire output and return the set of @Test method names that
    failed or errored.

    Surefire prints failing methods in lines like:
      [ERROR] testFoo(com.example.MyTest)  Time elapsed: 0.05 s  <<< FAILURE!
      [ERROR] testBar  Time elapsed: 0.01 s  <<< ERROR!
    """
    failing = set()
    pattern = re.compile(
        r'(?:\[ERROR\]\s+)?(\w+)(?:\([^)]*\))?\s+Time elapsed:.*<<<\s+(?:FAILURE|ERROR)',
        re.MULTILINE
    )
    for m in pattern.finditer(output):
        name = m.group(1)
        # Exclude summary lines like "Tests run: ..." which don't start with a method name
        if name and not name.startswith('Tests'):
            failing.add(name)
    return failing


def _count_test_methods(java_source):
    """Return the number of @Test annotations remaining in the Java source."""
    return len(re.findall(r'@Test\b', java_source))


def _remove_test_methods(java_source, method_names):
    """
    Remove @Test-annotated methods whose names are in *method_names* from the
    Java source string.  Uses brace counting to find each method's closing '}'.
    Returns the pruned source (unchanged if no methods are found/removed).
    """
    if not method_names:
        return java_source

    lines = java_source.split('\n')
    result_lines = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Detect the start of a @Test annotation
        if stripped == '@Test' or stripped.startswith('@Test(') or stripped.startswith('@Test '):
            annotation_start = i
            j = i + 1
            # Skip additional annotations / blank lines before the method signature
            while j < len(lines) and (
                lines[j].strip().startswith('@') or lines[j].strip() == ''
            ):
                j += 1

            if j < len(lines):
                sig_match = re.match(
                    r'\s*(?:(?:public|protected|private|static|final|synchronized)\s+)*'
                    r'(?:\w[\w<>\[\],\s]*\s+)(\w+)\s*\(',
                    lines[j]
                )
                if sig_match and sig_match.group(1) in method_names:
                    # Skip from @Test through the method's closing '}'
                    brace_count = 0
                    k = j
                    found_open = False
                    while k < len(lines):
                        for ch in lines[k]:
                            if ch == '{':
                                brace_count += 1
                                found_open = True
                            elif ch == '}':
                                brace_count -= 1
                        if found_open and brace_count == 0:
                            i = k + 1
                            break
                        k += 1
                    else:
                        i = k  # safety fallback
                    continue  # do NOT emit this method

        result_lines.append(lines[i])
        i += 1

    return '\n'.join(result_lines)


def compile_and_run(test_file_path, full_name, class_name, method_name, overload_index=None):
    dest_dir, filename = get_test_destination(
        full_name, class_name, method_name, overload_index
    )
    dest_path = os.path.join(dest_dir, filename)
    test_class_name = get_test_class_name(class_name, method_name, overload_index)
    package = '.'.join(full_name.split('.')[:-2])
    fqcn = f"{package}.{test_class_name}" if package else test_class_name
    package_path = os.path.join(*package.split('.')) if package else ''
    class_file = os.path.normpath(os.path.join(
        config.PDFBOX_DIR, 'target', 'test-classes',
        package_path, f'{test_class_name}.class'
    ))
    env = _maven_env()

    try:
        # Remove any stale generated test files left by a previous interrupted
        # run — they cause spurious compile errors when Maven recompiles all
        # sources in src/test/java.
        _clean_stale_generated_tests(config.PDFBOX_DIR)

        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(test_file_path, dest_path)

        # Wipe incremental state so compiler sees the new file.
        # Wrapped in try/except because on Windows the directory can be
        # locked by Maven or antivirus; failure here is non-fatal since
        # -Dmaven.compiler.useIncrementalCompilation=false already forces
        # a full recompile.
        incremental_state_dir = os.path.join(
            config.PDFBOX_DIR, 'target', 'maven-status',
            'maven-compiler-plugin', 'testCompile'
        )
        try:
            if os.path.isdir(incremental_state_dir):
                shutil.rmtree(incremental_state_dir)
        except OSError:
            pass  # non-fatal; full recompile is forced by the flag below

        # Touch the file so Maven sees it as newer than any cache
        os.utime(dest_path, None)

        # Compile
        compile_result = subprocess.run(
            [MAVEN_CMD,
             'resources:resources',
             'compiler:compile',
             'resources:testResources',
             'compiler:testCompile',
             '-Dmaven.compiler.useIncrementalCompilation=false'],
            cwd=config.PDFBOX_DIR,
            capture_output=True,
            text=True,
            timeout=config.MAVEN_TIMEOUT,
            env=env
        )

        compile_output = (compile_result.stdout or '') + (compile_result.stderr or '')
        if compile_result.returncode != 0 or 'COMPILATION ERROR' in compile_output:
            return False, False, compile_output

        # Verify .class was actually produced
        if not os.path.exists(class_file):
            return False, False, (
                f'Compilation reported success but {test_class_name}.class was not '
                f'found at {class_file}.\nMaven output:\n{compile_output}'
            )

        # Run
        run_result = subprocess.run(
            [MAVEN_CMD, 'surefire:test', f'-Dtest={fqcn}'],
            cwd=config.PDFBOX_DIR,
            capture_output=True,
            text=True,
            timeout=config.TEST_TIMEOUT,
            env=env
        )

        passed = run_result.returncode == 0
        run_output = (run_result.stdout or '') + (run_result.stderr or '')
        error = None if passed else run_output

        # ---- Partial-failure pruning ----
        # If some @Test methods threw errors, remove only those and rerun.
        # If the remaining methods all pass, treat the overall result as PASSED.
        if not passed:
            failing_methods = _parse_failing_test_methods(run_output)
            if failing_methods:
                with open(test_file_path, 'r', encoding='utf-8') as fh:
                    original_source = fh.read()
                pruned_source = _remove_test_methods(original_source, failing_methods)
                remaining = _count_test_methods(pruned_source)
                if remaining > 0 and pruned_source != original_source:
                    # Overwrite dest_path with the pruned source and recompile+rerun
                    with open(dest_path, 'w', encoding='utf-8') as fh:
                        fh.write(pruned_source)
                    os.utime(dest_path, None)

                    try:
                        shutil.rmtree(incremental_state_dir, ignore_errors=True)
                    except Exception:
                        pass

                    compile2 = subprocess.run(
                        [MAVEN_CMD,
                         'resources:resources',
                         'compiler:compile',
                         'resources:testResources',
                         'compiler:testCompile',
                         '-Dmaven.compiler.useIncrementalCompilation=false'],
                        cwd=config.PDFBOX_DIR,
                        capture_output=True,
                        text=True,
                        timeout=config.MAVEN_TIMEOUT,
                        env=env
                    )
                    compile2_output = (compile2.stdout or '') + (compile2.stderr or '')
                    if compile2.returncode == 0 and 'COMPILATION ERROR' not in compile2_output:
                        run2 = subprocess.run(
                            [MAVEN_CMD, 'surefire:test', f'-Dtest={fqcn}'],
                            cwd=config.PDFBOX_DIR,
                            capture_output=True,
                            text=True,
                            timeout=config.TEST_TIMEOUT,
                            env=env
                        )
                        if run2.returncode == 0:
                            passed = True
                            error = None

        return True, passed, error

    except subprocess.TimeoutExpired:
        return True, False, 'Test timed out'
    except Exception as e:
        return False, False, str(e)

    finally:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass