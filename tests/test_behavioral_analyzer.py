"""
Unit tests for src/behavioral_analyzer.py
Run with: pytest tests/test_behavioral_analyzer.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.behavioral_analyzer import extract_behavioral_constraints


# ---------------------------------------------------------------------------
# Throws — single
# ---------------------------------------------------------------------------

class TestSingleThrow:

    def test_throw_with_if_condition_extracted(self):
        body = """{
        if (fileName == null)
        {
            throw new IllegalArgumentException("fileName must not be null");
        }
        return doLoad(fileName);
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 1
        t = result['throws'][0]
        assert t['exception'] == 'IllegalArgumentException'
        assert t['condition'] == 'fileName == null'

    def test_throw_condition_with_method_call(self):
        """Condition contains a method call with its own parentheses."""
        body = """{
        if (arguments.isEmpty())
        {
            throw new MissingOperandException(operator, arguments);
        }
        process(arguments);
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 1
        t = result['throws'][0]
        assert t['exception'] == 'MissingOperandException'
        assert t['condition'] == 'arguments.isEmpty()'

    def test_throw_condition_with_negation_and_instanceof(self):
        """Condition contains negation and instanceof — nested parens."""
        body = """{
        if (!(base0 instanceof COSName))
        {
            throw new InvalidParameterException("expected COSName");
        }
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 1
        assert result['throws'][0]['condition'] == '!(base0 instanceof COSName)'

    def test_rethrow_excluded(self):
        """'throw ioe;' (re-throw) must NOT be captured — only 'throw new'."""
        body = """{
        try {
            return doWork();
        } catch (IOException ioe) {
            cleanup();
            throw ioe;
        }
    }"""
        result = extract_behavioral_constraints(body)
        assert result['throws'] == []

    def test_throw_without_guarding_if_has_none_condition(self):
        """An unconditional throw at the top level gets condition=None."""
        body = """{
        throw new UnsupportedOperationException("not implemented");
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 1
        assert result['throws'][0]['exception'] == 'UnsupportedOperationException'
        assert result['throws'][0]['condition'] is None


# ---------------------------------------------------------------------------
# Throws — multiple
# ---------------------------------------------------------------------------

class TestMultipleThrows:

    def test_two_throws_with_different_conditions(self):
        body = """{
        if (currentPage == null)
        {
            throw new IllegalStateException("no current page");
        }
        if (form == null)
        {
            throw new NullPointerException("form must not be null");
        }
        processStream(form);
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 2
        exceptions = {t['exception'] for t in result['throws']}
        assert exceptions == {'IllegalStateException', 'NullPointerException'}

        by_exc = {t['exception']: t for t in result['throws']}
        assert by_exc['IllegalStateException']['condition'] == 'currentPage == null'
        assert by_exc['NullPointerException']['condition'] == 'form == null'

    def test_nested_if_picks_innermost_condition(self):
        """The innermost (last) if in the lookback window is used."""
        body = """{
        if (colorSpace != null)
        {
            if (arguments.size() < colorSpace.getNumberOfComponents())
            {
                throw new MissingOperandException(op, arguments);
            }
        }
    }"""
        result = extract_behavioral_constraints(body)
        assert len(result['throws']) == 1
        # The INNERMOST guard (closest to throw) should be captured.
        assert result['throws'][0]['condition'] == 'arguments.size() < colorSpace.getNumberOfComponents()'


# ---------------------------------------------------------------------------
# Returns — boolean literals
# ---------------------------------------------------------------------------

class TestBooleanReturns:

    def test_return_true_captured(self):
        body = """{
        if (value > 0)
        {
            return true;
        }
        return false;
    }"""
        result = extract_behavioral_constraints(body)
        values = [r['value'] for r in result['returns']]
        assert 'true'  in values
        assert 'false' in values

    def test_return_false_context_is_the_line(self):
        body = """{
        if (isEmpty())
        {
            return false;
        }
        return true;
    }"""
        result = extract_behavioral_constraints(body)
        false_entry = next(r for r in result['returns'] if r['value'] == 'false')
        assert 'return false' in false_entry['context']

    def test_return_null_captured(self):
        body = """{
        if (map.containsKey(key))
        {
            return map.get(key);
        }
        return null;
    }"""
        result = extract_behavioral_constraints(body)
        values = [r['value'] for r in result['returns']]
        assert 'null' in values

    def test_return_integer_literal_captured(self):
        body = """{
        if (list.isEmpty())
        {
            return 0;
        }
        return list.size();
    }"""
        result = extract_behavioral_constraints(body)
        values = [r['value'] for r in result['returns']]
        assert '0' in values

    def test_return_string_literal_captured(self):
        body = """{
        if (name == null)
        {
            return "unknown";
        }
        return name;
    }"""
        result = extract_behavioral_constraints(body)
        values = [r['value'] for r in result['returns']]
        assert '"unknown"' in values

    def test_non_literal_return_not_captured(self):
        """'return parser.parse()' is not a literal and must be excluded."""
        body = """{
        FDFParser parser = new FDFParser(raFile);
        return parser.parse();
    }"""
        result = extract_behavioral_constraints(body)
        assert result['returns'] == []


# ---------------------------------------------------------------------------
# No throws or returns
# ---------------------------------------------------------------------------

class TestNoConstraints:

    def test_empty_body_returns_empty_dict(self):
        result = extract_behavioral_constraints('')
        assert result == {'throws': [], 'returns': []}

    def test_body_with_no_throw_or_literal_return(self):
        body = """{
        this.value = value;
        this.name = name;
    }"""
        result = extract_behavioral_constraints(body)
        assert result['throws']  == []
        assert result['returns'] == []

    def test_body_with_only_variable_return(self):
        body = """{
        int x = compute();
        return x;
    }"""
        result = extract_behavioral_constraints(body)
        assert result['returns'] == []


# ---------------------------------------------------------------------------
# None / falsy input
# ---------------------------------------------------------------------------

class TestNoneInput:

    def test_none_input(self):
        result = extract_behavioral_constraints(None)
        assert result == {'throws': [], 'returns': []}

    def test_none_has_correct_keys(self):
        result = extract_behavioral_constraints(None)
        assert 'throws'  in result
        assert 'returns' in result

    def test_empty_string_input(self):
        result = extract_behavioral_constraints('')
        assert result == {'throws': [], 'returns': []}
