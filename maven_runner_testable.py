"""
maven_runner_testable.py

Runtime shim that fixes three things blocking test compilation under
maven-compiler-plugin 3.14.0 + PDFBox's pom, without modifying
maven_runner.py or pom.xml on disk:

  1. Argv: replaces the plugin-goal sequence
       resources:resources compiler:compile resources:testResources compiler:testCompile
     with the lifecycle phase `test-compile`, so upstream phases
     (process-test-sources, build-helper add-test-source) run first.

  2. Pom override: regenerates pom_testable.xml next to pom.xml with every
     <testExcludes>...</testExcludes> block stripped out, and adds
     `-f pom_testable.xml` to the Maven argv. PDFBox's pom excludes
     ~14 packages from testCompile; without this override every generated
     test under those packages is silently dropped.

  3. Output cleanup: removes any prior `*_*_Test.class` from
     target/test-classes/ before each compile so the plugin can't decide
     "Nothing to compile - all classes are up to date".

pom.xml is read but never written. pom_testable.xml is regenerated on import
if missing or older than pom.xml.

Usage: imported by config_testable.py before pipeline_step3.
"""

import glob
import os
import re
import subprocess as _real_subprocess

import config
import src.maven_runner as _mr


_OLD_GOALS = (
    "resources:resources",
    "compiler:compile",
    "resources:testResources",
    "compiler:testCompile",
)
_NEW_PHASE = "test-compile"

# Directory pattern for stale generated test outputs.
_TEST_CLASSES_DIR = os.path.join(config.PDFBOX_DIR, "target", "test-classes")
_STALE_CLASS_GLOB = os.path.join(_TEST_CLASSES_DIR, "**", "*_*_Test.class")

# build-helper-maven-plugin adds pdfbox/tests_generated as a second test
# source root. Any leftover *_*_Test.java files there from prior runs cause
# the testCompile phase to fail with cascading errors, blocking new tests.
_TESTS_GENERATED_DIR = os.path.join(config.PDFBOX_DIR, "tests_generated")
_STALE_GENERATED_GLOB = os.path.join(_TESTS_GENERATED_DIR, "**", "*_*_Test.java")

# Pom override
_POM          = os.path.join(config.PDFBOX_DIR, "pom.xml")
_POM_TESTABLE = os.path.join(config.PDFBOX_DIR, "pom_testable.xml")
_TEST_EXCLUDES_RE = re.compile(
    r"<testExcludes>.*?</testExcludes>", re.DOTALL
)


def _ensure_pom_testable() -> None:
    """Generate pom_testable.xml from pom.xml with every <testExcludes> block
    stripped out. Skipped if the testable pom is already up-to-date."""
    if not os.path.exists(_POM):
        print(f"[maven_runner_testable] WARNING: pom.xml not found at {_POM}")
        return
    if (
        os.path.exists(_POM_TESTABLE)
        and os.path.getmtime(_POM_TESTABLE) >= os.path.getmtime(_POM)
    ):
        return
    with open(_POM, encoding="utf-8") as f:
        content = f.read()
    new_content, n = _TEST_EXCLUDES_RE.subn("", content)
    with open(_POM_TESTABLE, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(
        f"[maven_runner_testable] generated pom_testable.xml "
        f"({n} <testExcludes> blocks removed)"
    )


_ensure_pom_testable()


def _wipe_stale_test_classes() -> int:
    """Delete every `*_*_Test.class` under target/test-classes/.

    Mirrors maven_runner._clean_stale_generated_tests, but on the output
    side. Returns the number of files removed (used only for diagnostics).
    """
    if not os.path.isdir(_TEST_CLASSES_DIR):
        return 0
    n = 0
    for path in glob.glob(_STALE_CLASS_GLOB, recursive=True):
        try:
            os.remove(path)
            n += 1
        except OSError:
            pass
    return n


def _wipe_stale_generated_tests() -> int:
    """Delete every `*_*_Test.java` under pdfbox/tests_generated/.

    The directory is added as a test source root by build-helper. If any
    LLM-generated test file from a prior run lingers there with compile
    errors, it cascades and blocks new tests from compiling. Wiping before
    every compile is the safe play.
    """
    if not os.path.isdir(_TESTS_GENERATED_DIR):
        return 0
    n = 0
    for path in glob.glob(_STALE_GENERATED_GLOB, recursive=True):
        try:
            os.remove(path)
            n += 1
        except OSError:
            pass
    return n


def _is_compile_argv(args) -> bool:
    return (
        isinstance(args, (list, tuple))
        and len(args) >= 1 + len(_OLD_GOALS)
        and tuple(args[1 : 1 + len(_OLD_GOALS)]) == _OLD_GOALS
    )


class _PatchedSubprocess:
    """Passthrough proxy around the real subprocess module.

    Forwards every attribute (TimeoutExpired, PIPE, etc.) untouched. `run()`
    is intercepted only for compile-like argv; everything else passes through
    verbatim.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def run(self, args, *posargs, **kwargs):
        if _is_compile_argv(args):
            _wipe_stale_test_classes()
            _wipe_stale_generated_tests()
            args = [
                args[0],
                "-f", _POM_TESTABLE,
                _NEW_PHASE,
                *args[1 + len(_OLD_GOALS):],
            ]
        return self._real.run(args, *posargs, **kwargs)


_mr.subprocess = _PatchedSubprocess(_real_subprocess)
print(
    f"[maven_runner_testable] patched: {' '.join(_OLD_GOALS)}  ->  -f pom_testable.xml {_NEW_PHASE}; "
    f"stale {os.path.basename(_STALE_CLASS_GLOB)} cleaned before each compile"
)
