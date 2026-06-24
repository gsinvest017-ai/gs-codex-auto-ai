"""
test_repo_map.py — Tests for src/codexautoai_v2/repo_map.py

Covers:
1. extract_symbols — class + 2 functions: correct names / kinds / signatures
2. rank_symbols    — a symbol called by others ranks above an unused one
3. build_map       — respects max_chars, includes highest-ranked symbol
4. Empty inputs    — no crash, sane/empty outputs
"""
import pytest

from src.codexautoai_v2.repo_map import (
    Symbol,
    extract_symbols,
    rank_symbols,
    build_map,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_SOURCE = '''\
class MyClass(BaseModel):
    def method_a(self, x, y):
        return x + y

    def method_b(self):
        pass

def standalone(a, b=10, *args, **kwargs):
    pass

async def async_func(loop):
    await loop.run()
'''

CALLER_SOURCE = '''\
def caller():
    obj = MyClass()
    result = obj.method_a(1, 2)
    standalone(result)
    standalone(result)
    standalone(result)
    return result
'''

UNUSED_SOURCE = '''\
def never_called():
    pass
'''


# ---------------------------------------------------------------------------
# 1. extract_symbols
# ---------------------------------------------------------------------------

class TestExtractSymbols:
    def test_class_extracted(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        names = [s.name for s in syms]
        assert "MyClass" in names

    def test_class_kind(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        cls = next(s for s in syms if s.name == "MyClass")
        assert cls.kind == "class"

    def test_class_signature_has_base(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        cls = next(s for s in syms if s.name == "MyClass")
        assert "BaseModel" in cls.signature
        assert cls.signature.startswith("class MyClass(")

    def test_standalone_function_extracted(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        names = [s.name for s in syms]
        assert "standalone" in names

    def test_standalone_function_kind(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        fn = next(s for s in syms if s.name == "standalone")
        assert fn.kind == "function"

    def test_standalone_function_signature(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        fn = next(s for s in syms if s.name == "standalone")
        # signature must start with 'def standalone('
        assert fn.signature.startswith("def standalone(")
        # args present
        assert "a" in fn.signature
        assert "b=..." in fn.signature

    def test_async_function_extracted(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        names = [s.name for s in syms]
        assert "async_func" in names

    def test_async_function_signature_prefix(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        fn = next(s for s in syms if s.name == "async_func")
        assert fn.signature.startswith("async def async_func(")

    def test_method_extracted(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        names = [s.name for s in syms]
        assert "method_a" in names
        assert "method_b" in names

    def test_method_kind_is_function(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        m = next(s for s in syms if s.name == "method_a")
        assert m.kind == "function"

    def test_method_signature(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        m = next(s for s in syms if s.name == "method_a")
        assert "self" in m.signature
        assert "x" in m.signature
        assert "y" in m.signature

    def test_filename_stored(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        assert all(s.file == "sample.py" for s in syms)

    def test_lineno_positive(self):
        syms = extract_symbols(SAMPLE_SOURCE, "sample.py")
        assert all(s.lineno >= 1 for s in syms)

    def test_default_filename(self):
        syms = extract_symbols("def foo(): pass")
        assert syms[0].file == "<mem>"

    def test_syntax_error_returns_empty(self):
        syms = extract_symbols("def (broken syntax:", "bad.py")
        assert syms == []


# ---------------------------------------------------------------------------
# 2. rank_symbols
# ---------------------------------------------------------------------------

class TestRankSymbols:
    def test_called_symbol_ranks_above_unused(self):
        files = {
            "defs.py": "def popular(): pass\ndef unpopular(): pass\n",
            "user.py": "def caller():\n    popular()\n    popular()\n    popular()\n",
        }
        ranked = rank_symbols(files)
        names_in_order = [s.name for s, _ in ranked]
        assert "popular" in names_in_order
        assert "unpopular" in names_in_order
        assert names_in_order.index("popular") < names_in_order.index("unpopular")

    def test_reference_count_positive_for_called(self):
        files = {
            "a.py": "def foo(): pass\n",
            "b.py": "foo()\nfoo()\n",
        }
        ranked = rank_symbols(files)
        foo_entry = next((cnt for sym, cnt in ranked if sym.name == "foo"), None)
        assert foo_entry is not None
        assert foo_entry > 0

    def test_returns_list_of_tuples(self):
        files = {"f.py": "def fn(): pass\n"}
        ranked = rank_symbols(files)
        assert isinstance(ranked, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in ranked)

    def test_symbol_type_in_tuple(self):
        files = {"f.py": "class Cls: pass\n"}
        ranked = rank_symbols(files)
        sym, cnt = ranked[0]
        assert isinstance(sym, Symbol)
        assert isinstance(cnt, int)

    def test_multi_file_all_symbols_present(self):
        files = {
            "a.py": "def alpha(): pass\n",
            "b.py": "def beta(): pass\n",
        }
        ranked = rank_symbols(files)
        names = {s.name for s, _ in ranked}
        assert "alpha" in names
        assert "beta" in names

    def test_empty_files_dict(self):
        assert rank_symbols({}) == []

    def test_empty_source(self):
        ranked = rank_symbols({"empty.py": ""})
        assert ranked == []

    def test_class_and_method_both_ranked(self):
        files = {
            "mod.py": SAMPLE_SOURCE,
            "use.py": CALLER_SOURCE,
        }
        ranked = rank_symbols(files)
        names = {s.name for s, _ in ranked}
        assert "MyClass" in names
        assert "standalone" in names


# ---------------------------------------------------------------------------
# 3. build_map
# ---------------------------------------------------------------------------

class TestBuildMap:
    def test_output_within_budget(self):
        files = {
            "a.py": "def alpha(): pass\ndef beta(): pass\n",
            "b.py": "class Gamma: pass\n",
        }
        result = build_map(files, max_chars=200)
        assert len(result) <= 200

    def test_highest_ranked_symbol_present(self):
        files = {
            "defs.py": "def popular(): pass\ndef unpopular(): pass\n",
            "user.py": "def caller():\n    popular()\n    popular()\n    popular()\n    popular()\n",
        }
        result = build_map(files, max_chars=2000)
        assert "popular" in result

    def test_includes_filename_header(self):
        files = {"mymodule.py": "def fn(): pass\n"}
        result = build_map(files, max_chars=500)
        assert "mymodule.py" in result

    def test_large_budget_includes_all_symbols(self):
        files = {
            "f.py": "def aa(): pass\ndef bb(): pass\n",
        }
        result = build_map(files, max_chars=10000)
        assert "aa" in result
        assert "bb" in result

    def test_tiny_budget_does_not_crash(self):
        files = {"f.py": "def fn(): pass\n"}
        result = build_map(files, max_chars=1)
        # Should not raise; result may be empty or very short
        assert len(result) <= 50  # generous upper bound for edge behaviour

    def test_most_referenced_beats_low_referenced(self):
        files = {
            "lib.py": "def hot(): pass\ndef cold(): pass\n",
            "app.py": "\n".join(["hot()" for _ in range(20)]),
        }
        result = build_map(files, max_chars=2000)
        # hot must appear before cold (or cold may be absent if budget tiny)
        if "cold" in result:
            assert result.index("hot") < result.index("cold")

    def test_empty_files_returns_empty_string(self):
        assert build_map({}) == ""

    def test_empty_source_returns_empty_string(self):
        assert build_map({"empty.py": ""}) == ""

    def test_returns_string(self):
        files = {"x.py": "def f(): pass\n"}
        assert isinstance(build_map(files), str)

    def test_class_signature_in_output(self):
        files = {"mod.py": "class Foo(Bar): pass\n"}
        result = build_map(files, max_chars=500)
        assert "class Foo" in result

    def test_function_signature_with_args_in_output(self):
        files = {"mod.py": "def greet(name, greeting='hello'): pass\n"}
        result = build_map(files, max_chars=500)
        assert "def greet(" in result


# ---------------------------------------------------------------------------
# 4. Empty / edge inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_extract_empty_source(self):
        assert extract_symbols("") == []

    def test_extract_whitespace_only(self):
        assert extract_symbols("   \n\t\n") == []

    def test_rank_single_empty_file(self):
        assert rank_symbols({"x.py": ""}) == []

    def test_build_map_single_empty_file(self):
        assert build_map({"x.py": ""}) == ""

    def test_extract_class_no_bases(self):
        syms = extract_symbols("class Plain: pass\n")
        assert len(syms) == 1
        assert syms[0].signature == "class Plain"

    def test_extract_class_multiple_bases(self):
        syms = extract_symbols("class Multi(A, B, C): pass\n")
        assert len(syms) == 1
        assert "A" in syms[0].signature
        assert "B" in syms[0].signature
        assert "C" in syms[0].signature

    def test_extract_function_no_args(self):
        syms = extract_symbols("def no_args(): pass\n")
        assert syms[0].signature == "def no_args()"

    def test_extract_function_vararg(self):
        syms = extract_symbols("def variadic(*args, **kwargs): pass\n")
        sig = syms[0].signature
        assert "*args" in sig
        assert "**kwargs" in sig

    def test_rank_symbols_no_crash_on_syntax_error_file(self):
        files = {
            "bad.py": "def (broken:",
            "good.py": "def fine(): pass\n",
        }
        # Should not raise
        ranked = rank_symbols(files)
        assert isinstance(ranked, list)

    def test_build_map_no_crash_syntax_error(self):
        files = {
            "bad.py": "def (broken:",
            "good.py": "def fine(): pass\n",
        }
        result = build_map(files, max_chars=500)
        assert isinstance(result, str)
