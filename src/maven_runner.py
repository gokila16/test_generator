import os
import sys
import shutil
import subprocess
import config
from src.file_manager import get_test_destination, get_test_class_name

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

def compile_and_run(test_file_path, full_name, class_name, method_name, overload_index=None):
    dest_dir, filename = get_test_destination(
        full_name, class_name, method_name, overload_index
    )
    dest_path = os.path.join(dest_dir, filename)
    test_class_name = get_test_class_name(class_name, method_name, overload_index)
    env = _maven_env()

    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(test_file_path, dest_path)

        # Invoke compiler plugin directly to bypass lifecycle-bound plugins (e.g. checkstyle)
        compile_result = subprocess.run(
            [MAVEN_CMD,
             'resources:resources',
             'compiler:compile',
             'resources:testResources',
             'compiler:testCompile',
             '-q'],
            cwd=config.PDFBOX_DIR,
            capture_output=True,
            text=True,
            timeout=config.MAVEN_TIMEOUT,
            env=env
        )

        if compile_result.returncode != 0:
            return False, False, (compile_result.stderr
                                  or compile_result.stdout)

        run_result = subprocess.run(
            [
                MAVEN_CMD, 'surefire:test',
                f'-Dtest={test_class_name}',
                '-q'
            ],
            cwd=config.PDFBOX_DIR,
            capture_output=True,
            text=True,
            timeout=config.TEST_TIMEOUT,
            env=env
        )

        passed = run_result.returncode == 0
        error  = None if passed else (run_result.stderr
                                      or run_result.stdout)
        return True, passed, error

    except subprocess.TimeoutExpired:
        return True, False, 'Test timed out'
    except Exception as e:
        return False, False, str(e)

    finally:
        if os.path.exists(dest_path):
            os.remove(dest_path)