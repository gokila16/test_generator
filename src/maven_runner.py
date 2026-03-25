import cmd
import os
import shutil
import subprocess
import config


def get_test_destination(full_name, class_name):
    """
    Gets destination path in src/test/java matching package structure
    e.g. org.apache.pdfbox.multipdf.PDFMergerUtility.appendDocument
    -> src/test/java/org/apache/pdfbox/multipdf/PDFMergerUtilityTest.java
    """
    parts = full_name.split('.')
    package_parts = parts[:-2]  # remove class name and method name
    package_path = os.path.join(*package_parts)

    dest_dir = os.path.join(
        config.PDFBOX_DIR,
        'src', 'test', 'java',
        package_path
    )
    return dest_dir, f"{class_name}Test.java"


def compile_and_run(test_file_path, full_name, class_name):
    """
    1. Copies test to src/test/java
    2. Compiles with mvn test-compile
    3. Runs only that specific test with mvn test
    4. Always deletes from src/test/java after
    Returns (compiled, passed, error_message)
    """
    dest_dir, filename = get_test_destination(full_name, class_name)
    dest_path = os.path.join(dest_dir, filename)
    test_class_name = f"{class_name}Test"

    try:
        # Step 1: Copy to src/test/java
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(test_file_path, dest_path)

        # Step 2: Compile
        cmd = ['mvn', 'test-compile', '-q', '-Dcheckstyle.skip=true']
        print(f"  Running: {' '.join(cmd)}")
        compile_result = subprocess.run(
            cmd,
            cwd=config.PDFBOX_DIR,
            capture_output=True,
            text=True,
            timeout=config.MAVEN_TIMEOUT
        )

        if compile_result.returncode != 0:
            return False, False, (compile_result.stderr
                                  or compile_result.stdout)
       

        # Step 3: Run only this specific test
        run_result = subprocess.run(
            [
                'mvn', 'test',
                f'-Dtest={test_class_name}',
                '-q',
                '-Dcheckstyle.skip=true'
        ],
        cwd=config.PDFBOX_DIR,
        capture_output=True,
        text=True,
        timeout=config.TEST_TIMEOUT
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
        # Step 4: Always delete from src/test/java after
        if os.path.exists(dest_path):
            os.remove(dest_path)
            print(f"  Deleted successfully")
        else:
            print(f"  File not found at dest_path")