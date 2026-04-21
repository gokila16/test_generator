"""
Unit tests for src/java_post_processor.py
Run with: pytest tests/test_post_processor.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from src.java_post_processor import (
    _is_sut_missing,
    _check_class_redefinition,
    _ensure_package,
    _collapse_url_variable,
    _check_test_class_name,
    _has_test_methods,
    _check_constructor_arities,
    post_process_java,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_test_class(class_name="MyClass_myMethod_Test", sut="MyClass", package="com.example"):
    """Returns a minimal, valid Java test string."""
    return f"""\
package {package};

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class {class_name} {{

    @Test
    void happyPath() throws Exception {{
        {sut} obj = new {sut}();
        assertNotNull(obj);
        assertEquals(42, obj.getValue());
    }}
}}
"""


# ---------------------------------------------------------------------------
# _is_sut_missing
# ---------------------------------------------------------------------------

class TestIsSutMissing:

    def test_sut_present_as_type(self):
        code = "public class FooTest {\n    @Test void t() { Foo f = new Foo(); }\n}"
        assert _is_sut_missing(code, "Foo") is False

    def test_sut_present_as_constructor(self):
        code = "public class FooTest {\n    @Test void t() { new Foo(1, 2); }\n}"
        assert _is_sut_missing(code, "Foo") is False

    def test_sut_present_as_static_call(self):
        code = "public class FooTest {\n    @Test void t() { Foo.create(); }\n}"
        assert _is_sut_missing(code, "Foo") is False

    def test_sut_absent(self):
        code = "public class FooTest {\n    @Test void t() { Bar b = new Bar(); }\n}"
        assert _is_sut_missing(code, "Foo") is True

    def test_sut_only_in_import_line_counts_as_missing(self):
        """An import does not count as usage — must appear in the code body."""
        code = (
            "import com.example.Foo;\n"
            "public class FooTest {\n"
            "    @Test void t() { Bar b = new Bar(); }\n"
            "}"
        )
        assert _is_sut_missing(code, "Foo") is True

    def test_sut_only_in_package_line_counts_as_missing(self):
        code = (
            "package com.example.Foo;\n"
            "public class FooTest {\n"
            "    @Test void t() { Bar b = new Bar(); }\n"
            "}"
        )
        assert _is_sut_missing(code, "Foo") is True

    def test_partial_name_does_not_match(self):
        """'FooBar' should not satisfy a search for 'Foo'."""
        code = "public class FooTest {\n    @Test void t() { FooBar fb = new FooBar(); }\n}"
        assert _is_sut_missing(code, "Foo") is True


# ---------------------------------------------------------------------------
# _check_class_redefinition
# ---------------------------------------------------------------------------

class TestCheckClassRedefinition:

    def test_only_test_class_is_clean(self):
        code = "public class MyTest {\n    @Test void t() {}\n}"
        assert _check_class_redefinition(code, "MyTest") == []

    def test_extra_top_level_class_detected(self):
        code = (
            "public class MyTest {\n    @Test void t() {}\n}\n"
            "class COSArray {\n    int size() { return 0; }\n}"
        )
        extras = _check_class_redefinition(code, "MyTest")
        assert "COSArray" in extras

    def test_extra_top_level_interface_detected(self):
        code = "public class MyTest {}\ninterface Fooable {}"
        extras = _check_class_redefinition(code, "MyTest")
        assert "Fooable" in extras

    def test_extra_top_level_enum_detected(self):
        code = "public class MyTest {}\nenum Color { RED, GREEN }"
        extras = _check_class_redefinition(code, "MyTest")
        assert "Color" in extras

    def test_inner_class_not_flagged(self):
        """An inner class is indented, so ^ does not match it."""
        code = (
            "public class MyTest {\n"
            "    private static class Helper {}\n"
            "    @Test void t() {}\n"
            "}"
        )
        assert _check_class_redefinition(code, "MyTest") == []

    def test_inner_enum_not_flagged(self):
        code = (
            "public class MyTest {\n"
            "    private enum State { OPEN, CLOSED }\n"
            "    @Test void t() {}\n"
            "}"
        )
        assert _check_class_redefinition(code, "MyTest") == []


# ---------------------------------------------------------------------------
# _ensure_package
# ---------------------------------------------------------------------------

class TestEnsurePackage:

    def test_no_package_declaration_gets_injected(self):
        code = "public class Foo {}"
        result = _ensure_package(code, "com.example")
        assert result.startswith("package com.example;")
        assert "public class Foo" in result

    def test_existing_package_not_duplicated(self):
        code = "package com.example;\npublic class Foo {}"
        result = _ensure_package(code, "com.example")
        assert result.count("package") == 1

    def test_comment_before_package_not_duplicated(self):
        """A leading comment must not fool _ensure_package into injecting a second package."""
        code = "// Auto-generated\npackage com.example;\npublic class Foo {}"
        result = _ensure_package(code, "com.example")
        assert result.count("package") == 1

    def test_empty_expected_package_is_noop(self):
        code = "public class Foo {}"
        assert _ensure_package(code, "") == code
        assert _ensure_package(code, None) == code

    def test_block_comment_before_package_not_duplicated(self):
        code = "/* header */\npackage com.example;\npublic class Foo {}"
        result = _ensure_package(code, "com.example")
        assert result.count("package") == 1


# ---------------------------------------------------------------------------
# _collapse_url_variable
# ---------------------------------------------------------------------------

class TestCollapseUrlVariable:

    def test_adjacent_lines_collapsed(self):
        code = (
            "        URL url = getClass().getClassLoader().getResource(\"file.pdf\");\n"
            "        File file = new File(url.toURI());\n"
        )
        result = _collapse_url_variable(code)
        assert "URL url" not in result
        assert 'new File(getClass().getClassLoader().getResource("file.pdf").toURI())' in result

    def test_blank_line_between_statements_collapsed(self):
        code = (
            "        URL url = getClass().getClassLoader().getResource(\"file.pdf\");\n"
            "\n"
            "        File file = new File(url.toURI());\n"
        )
        result = _collapse_url_variable(code)
        assert "URL url" not in result
        assert 'new File(getClass().getClassLoader().getResource("file.pdf").toURI())' in result

    def test_non_matching_pattern_left_unchanged(self):
        code = "File file = new File(\"path/to/file\");\n"
        assert _collapse_url_variable(code) == code

    def test_indent_preserved(self):
        code = (
            "    URL u = getClass().getClassLoader().getResource(\"x.pdf\");\n"
            "    File f = new File(u.toURI());\n"
        )
        result = _collapse_url_variable(code)
        assert result.startswith("    File f")


# ---------------------------------------------------------------------------
# _check_test_class_name
# ---------------------------------------------------------------------------

class TestCheckTestClassName:

    def test_matching_name_passes(self):
        code = "public class Foo_bar_Test {\n    @Test void t() {}\n}"
        ok, actual = _check_test_class_name(code, "Foo_bar_Test")
        assert ok is True
        assert actual is None

    def test_mismatching_name_fails(self):
        code = "public class WrongName {\n    @Test void t() {}\n}"
        ok, actual = _check_test_class_name(code, "Foo_bar_Test")
        assert ok is False
        assert actual == "WrongName"

    def test_no_public_class_fails(self):
        code = "class Foo_bar_Test {\n    @Test void t() {}\n}"
        ok, actual = _check_test_class_name(code, "Foo_bar_Test")
        assert ok is False
        assert actual is None


# ---------------------------------------------------------------------------
# _has_test_methods
# ---------------------------------------------------------------------------

class TestHasTestMethods:

    def test_file_with_test_annotation_passes(self):
        code = "public class T {\n    @Test\n    void foo() {}\n}"
        assert _has_test_methods(code) is True

    def test_file_without_test_annotation_rejected(self):
        code = "public class T {\n    void foo() {}\n}"
        assert _has_test_methods(code) is False

    def test_multiple_test_annotations(self):
        code = "public class T {\n    @Test void a() {}\n    @Test void b() {}\n}"
        assert _has_test_methods(code) is True

    def test_test_in_comment_does_not_count(self):
        # @Test appears only in a comment — should not count
        code = "public class T {\n    // @Test\n    void foo() {}\n}"
        assert _has_test_methods(code) is False


# ---------------------------------------------------------------------------
# post_process_java — end-to-end
# ---------------------------------------------------------------------------

class TestPostProcessJava:

    def test_minimal_valid_test_passes(self):
        code = _minimal_test_class()
        result, reason = post_process_java(
            code,
            expected_package="com.example",
            test_class_name="MyClass_myMethod_Test",
            sut_class_name="MyClass",
        )
        assert result is not None
        assert reason is None
        assert "package com.example;" in result
        assert "@Test" in result

    def test_none_input_returns_none_no_reason(self):
        result, reason = post_process_java(None)
        assert result is None
        assert reason is None

    def test_empty_string_returns_empty_no_reason(self):
        result, reason = post_process_java("")
        assert result == ""
        assert reason is None

    def test_rejected_when_sut_missing(self):
        code = _minimal_test_class(sut="MyClass")
        # Claim the SUT is something that does not appear in the code
        result, reason = post_process_java(code, sut_class_name="NonExistentClass")
        assert result is None
        assert reason == "sut_missing"

    def test_class_name_mismatch_not_rejected_by_post_processor(self):
        # _check_test_class_name is intentionally NOT wired into post_process_java.
        # The LLM sometimes generates variant names (e.g. with overload suffixes) and
        # the Java compiler enforces filename==classname at compile time anyway.
        code = _minimal_test_class(class_name="MyClass_myMethod_Test")
        result, reason = post_process_java(
            code,
            test_class_name="DifferentClass_myMethod_Test",
            sut_class_name="MyClass",
        )
        assert result is not None  # should pass through; compiler catches the real mismatch
        assert reason is None

    def test_rejected_when_no_test_methods(self):
        code = (
            "package com.example;\n"
            "public class MyClass_myMethod_Test {\n"
            "    void notATest() { int x = 1; }\n"
            "}\n"
        )
        result, reason = post_process_java(code, test_class_name="MyClass_myMethod_Test")
        assert result is None
        assert reason == "no_test_methods"

    def test_package_injected_when_missing(self):
        code = (
            "import org.junit.jupiter.api.Test;\n"
            "public class MyClass_myMethod_Test {\n"
            "    @Test void t() throws Exception { MyClass obj = new MyClass(); assertEquals(1, obj.val()); }\n"
            "}\n"
        )
        result, reason = post_process_java(
            code,
            expected_package="org.example",
            test_class_name="MyClass_myMethod_Test",
            sut_class_name="MyClass",
        )
        assert result is not None
        assert reason is None
        assert result.startswith("package org.example;")

    def test_throws_exception_added_to_test_method(self):
        code = (
            "package com.example;\n"
            "public class Foo_bar_Test {\n"
            "    @Test\n"
            "    void doSomething() {\n"
            "        Foo f = new Foo();\n"
            "        assertEquals(1, f.bar());\n"
            "    }\n"
            "}\n"
        )
        result, reason = post_process_java(
            code,
            test_class_name="Foo_bar_Test",
            sut_class_name="Foo",
        )
        assert result is not None
        assert reason is None
        assert "throws Exception" in result

# ---------------------------------------------------------------------------
# _check_constructor_arities
# ---------------------------------------------------------------------------

class TestCheckConstructorArities:

    # Inventory stub: PDFTextStripper has a single no-arg constructor.
    _INVENTORY = {
        'org.apache.pdfbox.text.PDFTextStripper': {
            'class_name': 'PDFTextStripper',
            'full_name':  'org.apache.pdfbox.text.PDFTextStripper',
            'constructors': [
                {'params': [], 'visibility': 'public'},
            ],
        },
        'com.example.MultiCtor': {
            'class_name': 'MultiCtor',
            'full_name':  'com.example.MultiCtor',
            'constructors': [
                {'params': [], 'visibility': 'public'},
                {'params': ['String'], 'visibility': 'public'},
                {'params': ['String', 'int'], 'visibility': 'public'},
            ],
        },
    }

    def test_valid_no_arg_constructor_passes(self):
        code = "PDFTextStripper stripper = new PDFTextStripper();"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is None

    def test_wrong_arity_returns_constructor_arity_message(self):
        # PDFTextStripper only has a 0-arg constructor; calling with 1 arg is wrong.
        code = "PDFTextStripper stripper = new PDFTextStripper(writer);"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is not None
        assert reason.startswith("constructor_arity:")
        assert "PDFTextStripper" in reason
        assert "1" in reason
        assert "[0]" in reason

    def test_unknown_class_is_skipped(self):
        # UnknownClass is not in the inventory — should silently pass.
        code = "UnknownClass obj = new UnknownClass(a, b, c);"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is None

    def test_empty_inventory_skips_all_checks(self):
        code = "PDFTextStripper s = new PDFTextStripper(badArg);"
        _, reason = _check_constructor_arities(code, {})
        assert reason is None

    def test_none_inventory_skips_all_checks(self):
        code = "PDFTextStripper s = new PDFTextStripper(badArg);"
        _, reason = _check_constructor_arities(code, None)
        assert reason is None

    def test_multi_ctor_valid_two_arg_passes(self):
        code = "MultiCtor mc = new MultiCtor(\"hello\", 42);"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is None

    def test_multi_ctor_invalid_three_arg_fails(self):
        code = "MultiCtor mc = new MultiCtor(a, b, c);"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is not None
        assert reason.startswith("constructor_arity:")

    def test_nested_parens_counted_as_one_arg(self):
        # new PDFTextStripper(foo(a, b)) — nested commas should not be counted.
        code = "PDFTextStripper s = new PDFTextStripper(foo(a, b));"
        _, reason = _check_constructor_arities(code, self._INVENTORY)
        assert reason is not None
        assert "1" in reason  # one argument despite two commas inside

    def test_post_process_java_rejects_wrong_arity(self):
        """End-to-end: wrong-arity constructor fails post_process_java."""
        code = (
            "package com.example;\n"
            "import org.junit.jupiter.api.Test;\n"
            "import static org.junit.jupiter.api.Assertions.*;\n"
            "public class PDFTextStripper_strip_Test {\n"
            "    @Test\n"
            "    void test() throws Exception {\n"
            "        PDFTextStripper s = new PDFTextStripper(new StringWriter());\n"
            "        assertNotNull(s);\n"
            "    }\n"
            "}\n"
        )
        result, reason = post_process_java(
            code,
            test_class_name="PDFTextStripper_strip_Test",
            sut_class_name="PDFTextStripper",
            class_inventory=self._INVENTORY,
        )
        assert result is None
        assert reason is not None
        assert reason.startswith("constructor_arity:")

    def test_post_process_java_passes_correct_arity(self):
        """End-to-end: correct no-arg constructor passes post_process_java."""
        code = (
            "package com.example;\n"
            "import org.junit.jupiter.api.Test;\n"
            "import static org.junit.jupiter.api.Assertions.*;\n"
            "public class PDFTextStripper_strip_Test {\n"
            "    @Test\n"
            "    void test() throws Exception {\n"
            "        PDFTextStripper s = new PDFTextStripper();\n"
            "        assertNotNull(s);\n"
            "    }\n"
            "}\n"
        )
        result, reason = post_process_java(
            code,
            test_class_name="PDFTextStripper_strip_Test",
            sut_class_name="PDFTextStripper",
            class_inventory=self._INVENTORY,
        )
        assert result is not None
        assert reason is None


# ---------------------------------------------------------------------------

    def test_rejected_when_extra_class_defined(self):
        code = (
            "package com.example;\n"
            "public class MyClass_myMethod_Test {\n"
            "    @Test void t() { MyClass m = new MyClass(); assertEquals(1, m.x()); }\n"
            "}\n"
            "class COSArray { int size() { return 0; } }\n"
        )
        result, reason = post_process_java(
            code,
            test_class_name="MyClass_myMethod_Test",
            sut_class_name="MyClass",
        )
        assert result is None
        assert reason == "class_redefinition"
