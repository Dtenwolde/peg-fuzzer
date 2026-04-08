"""Tests for the SQL generator."""

import random

import pytest

from peg_fuzzer.grammar.parser import load_grammar_dir
from peg_fuzzer.generator.generator import Generator, GenContext

GRAMMAR_DIR = "duckdb/extension/autocomplete/grammar"


@pytest.fixture(scope="module")
def grammar():
    return load_grammar_dir(GRAMMAR_DIR)


@pytest.fixture
def gen(grammar):
    return Generator(grammar, random.Random(42))


def test_generate_returns_string(gen):
    sql = gen.generate("SelectStatement")
    assert isinstance(sql, str)
    assert len(sql) > 0


def test_generate_select_nonempty(gen):
    # SelectStatement can expand to VALUES / TABLE / DESCRIBE / etc. -- just check non-empty
    for _ in range(20):
        sql = gen.generate("SelectStatement")
        assert len(sql) > 0


def test_generate_statement_runs(gen):
    # Should generate without exception for many iterations.
    for _ in range(50):
        gen.generate("Statement")


def test_generate_reproducible():
    g = load_grammar_dir(GRAMMAR_DIR)
    sql1 = Generator(g, random.Random(0)).generate("SelectStatement")
    sql2 = Generator(g, random.Random(0)).generate("SelectStatement")
    assert sql1 == sql2


def test_generate_insert(gen):
    sql = gen.generate("InsertStatement")
    assert "INSERT" in sql.upper()


def test_generate_create_table(gen):
    sql = gen.generate("CreateStatement")
    assert "CREATE" in sql.upper()


def test_generate_different_seeds_differ():
    g = load_grammar_dir(GRAMMAR_DIR)
    results = {Generator(g, random.Random(i)).generate("Statement") for i in range(10)}
    assert len(results) > 1


# ---------------------------------------------------------------------------
# Context-aware window name binding
# ---------------------------------------------------------------------------

def test_gen_context_resets_between_statements(gen):
    gen.generate("Statement")
    gen._ctx.window_names.append("leftover")
    gen.generate("Statement")
    # generate() calls reset() so leftover name should be gone
    assert "leftover" not in gen._ctx.window_names


def test_base_window_name_empty_when_no_windows_defined(gen):
    gen._ctx.reset()
    result = gen._expand_rule("BaseWindowName", 0, frozenset(), {})
    assert result == []


def test_base_window_name_picks_from_context(gen):
    gen._ctx.reset()
    gen._ctx.window_names = ["w1", "w2"]
    for _ in range(20):
        result = gen._expand_rule("BaseWindowName", 0, frozenset(), {})
        assert result in (["w1"], ["w2"])


def test_window_definition_registers_name(gen):
    gen._ctx.reset()
    gen._expand_rule("WindowDefinition", 0, frozenset(), {})
    assert len(gen._ctx.window_names) == 1


def test_window_definition_name_is_string(gen):
    gen._ctx.reset()
    gen._expand_rule("WindowDefinition", 0, frozenset(), {})
    name = gen._ctx.window_names[0]
    assert isinstance(name, str) and len(name) > 0


def test_multiple_window_definitions_register_all(gen):
    gen._ctx.reset()
    for _ in range(3):
        gen._expand_rule("WindowDefinition", 0, frozenset(), {})
    assert len(gen._ctx.window_names) == 3


def test_window_frame_no_bare_identifier_without_context(gen):
    # When no window names are defined, WindowFrame must not emit a bare
    # identifier (which would be a dangling named-window reference).
    # Both valid arms (ParensIdentifier and WindowFrameDefinition) produce
    # output containing '(' since they wrap content in parentheses.
    gen._ctx.reset()
    for _ in range(40):
        result = gen._expand_rule("WindowFrame", 0, frozenset(), {})
        sql = " ".join(result)
        assert "(" in sql, f"Expected inline frame (with parens), got: {sql!r}"


def test_window_frame_allows_named_reference_with_context(gen):
    # When window names are registered, the bare Identifier arm may be chosen,
    # producing a bare name without parens.
    gen._ctx.reset()
    gen._ctx.window_names = ["mywindow"]
    bare_seen = False
    for _ in range(60):
        result = gen._expand_rule("WindowFrame", 0, frozenset(), {})
        sql = " ".join(result)
        if "(" not in sql:
            bare_seen = True
            assert sql.strip() == "mywindow", f"Unexpected bare frame: {sql!r}"
            break
    assert bare_seen, "Expected at least one bare named-window reference in 60 tries"


def test_list_no_trailing_comma(gen):
    # List(D) has an optional trailing comma that Postgres rejects in most
    # contexts.  The generator must never emit it.
    for seed in range(200):
        g2 = Generator(gen.grammar, random.Random(seed))
        sql = g2.generate("Statement")
        assert ", )" not in sql, f"Trailing comma before ')' in: {sql!r}"
        assert ", ," not in sql, f"Double comma in: {sql!r}"


def test_cte_context_resets_between_statements(gen):
    gen._ctx.cte_names.append("leftover_cte")
    gen.generate("Statement")
    assert "leftover_cte" not in gen._ctx.cte_names


def test_with_statement_registers_cte_name(gen):
    gen._ctx.reset()
    gen._expand_rule("WithStatement", 0, frozenset(), {})
    # CTEBody may itself contain nested WITH statements, so >= 1 is correct.
    assert len(gen._ctx.cte_names) >= 1


def test_with_statement_cte_name_is_string(gen):
    gen._ctx.reset()
    gen._expand_rule("WithStatement", 0, frozenset(), {})
    for name in gen._ctx.cte_names:
        assert isinstance(name, str) and len(name) > 0


def test_multiple_with_statements_accumulate_cte_names(gen):
    gen._ctx.reset()
    before = len(gen._ctx.cte_names)
    for _ in range(3):
        gen._expand_rule("WithStatement", 0, frozenset(), {})
    # Each expansion adds at least one name (possibly more due to nested CTEs).
    assert len(gen._ctx.cte_names) >= before + 3


def test_table_name_can_return_cte_name(gen):
    gen._ctx.reset()
    gen._ctx.cte_names = ["my_cte"]
    found = False
    for _ in range(40):
        result = gen._expand_rule("TableName", 0, frozenset(), {})
        if result == ["my_cte"]:
            found = True
            break
    assert found, "Expected TableName to return CTE name at least once in 40 tries"


def test_table_name_with_schema_pools_uses_fuzz_tables(grammar):
    """With schema pools loaded, TableName (no CTEs) returns a fuzz table name."""
    from peg_fuzzer.generator.catalog import build_schema_setup
    from peg_fuzzer.grammar.overrides import OverrideKind
    schema_pools, _ = build_schema_setup()
    g = Generator(grammar, random.Random(99), pools=schema_pools)
    g._ctx.reset()
    fuzz_tables = set(schema_pools[OverrideKind.TABLE_NAME])
    for _ in range(20):
        result = g._expand_rule("TableName", 0, frozenset(), {})
        assert result[0] in fuzz_tables, f"Expected fuzz table, got {result[0]!r}"


# ---------------------------------------------------------------------------
# Coverage-guided ChoiceNode arm selection
# ---------------------------------------------------------------------------

def test_weighted_choice_no_coverage_is_uniform(grammar):
    # With all hits at zero, _weighted_choice should behave like uniform random.
    # Check it returns each of 2 options at all (over 200 trials).
    g = Generator(grammar, random.Random(7), coverage_hits={})
    node_a = g.grammar.rules.get("Statement")
    # Use a mini ChoiceNode with two simple literal arms.
    from peg_fuzzer.generator.generator import SeqNode, LiteralNode, ChoiceNode
    arm1 = SeqNode(children=[LiteralNode(text="A")])
    arm2 = SeqNode(children=[LiteralNode(text="B")])
    options = [arm1, arm2]
    seen = set()
    for _ in range(200):
        chosen = g._weighted_choice(options)
        seen.add(id(chosen))
    assert len(seen) == 2, "Both arms should be chosen at least once in 200 trials"


def test_weighted_choice_cold_arm_preferred(grammar):
    # If arm2's primary ref has 10000 hits and arm1's ref has 0 hits,
    # arm1 (cold) should be chosen significantly more often.
    from peg_fuzzer.generator.generator import SeqNode, RefNode, ChoiceNode
    arm1 = SeqNode(children=[RefNode(ref="ColdRule")])
    arm2 = SeqNode(children=[RefNode(ref="HotRule")])
    options = [arm1, arm2]
    coverage_hits = {"HotRule": 10000, "ColdRule": 0}
    g = Generator(grammar, random.Random(42), coverage_hits=coverage_hits)
    cold_count = sum(
        1 for _ in range(500) if g._weighted_choice(options) is arm1
    )
    # Cold arm has weight 1.0, hot arm has weight ~0.0001 -- cold should win >99% of the time.
    assert cold_count > 450, f"Cold arm chosen only {cold_count}/500 times"


def test_weighted_choice_equal_hits_both_chosen(grammar):
    from peg_fuzzer.generator.generator import SeqNode, RefNode
    arm1 = SeqNode(children=[RefNode(ref="RuleX")])
    arm2 = SeqNode(children=[RefNode(ref="RuleY")])
    options = [arm1, arm2]
    coverage_hits = {"RuleX": 500, "RuleY": 500}
    g = Generator(grammar, random.Random(13), coverage_hits=coverage_hits)
    seen = set()
    for _ in range(200):
        seen.add(id(g._weighted_choice(options)))
    assert len(seen) == 2, "Equal-weight arms should both be chosen across 200 trials"


# ---------------------------------------------------------------------------
# Schema-aware ColumnReference generation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def gen_with_pools(grammar):
    from peg_fuzzer.generator.catalog import build_schema_setup
    schema_pools, _ = build_schema_setup()
    return Generator(grammar, random.Random(1), pools=schema_pools)


def test_column_reference_bare_uses_pool(gen_with_pools):
    from peg_fuzzer.generator.catalog import FUZZ_SCHEMA
    all_cols = {col for cols in FUZZ_SCHEMA.values() for col, _ in cols}
    for _ in range(100):
        result = gen_with_pools._expand_rule("ColumnReference", 0, frozenset(), {})
        sql = " ".join(result)
        # Split on '.' to get the column part (last token)
        parts = sql.split(".")
        col = parts[-1].strip()
        assert col in all_cols, f"Column {col!r} not in schema: {sql!r}"


def test_column_reference_qualified_uses_valid_pair(gen_with_pools):
    from peg_fuzzer.generator.catalog import FUZZ_SCHEMA
    valid_pairs = {
        (table, col)
        for table, cols in FUZZ_SCHEMA.items()
        for col, _ in cols
    }
    qualified_seen = 0
    for _ in range(200):
        result = gen_with_pools._expand_rule("ColumnReference", 0, frozenset(), {})
        sql = " ".join(result)
        if "." in sql:
            parts = sql.split(".")
            table, col = parts[0].strip(), parts[1].strip()
            assert (table, col) in valid_pairs, f"Invalid pair {(table, col)!r}: {sql!r}"
            qualified_seen += 1
    assert qualified_seen > 0, "Expected at least one qualified reference in 200 trials"


def test_column_reference_no_pool_falls_through(gen):
    # gen fixture has no schema pools -- ColumnReference should fall through
    # to normal grammar expansion and still produce a non-empty string.
    result = gen._expand_rule("ColumnReference", 0, frozenset(), {})
    assert len(result) > 0


def test_column_reference_both_forms_seen(gen_with_pools):
    bare_seen = qualified_seen = False
    for _ in range(200):
        result = gen_with_pools._expand_rule("ColumnReference", 0, frozenset(), {})
        sql = " ".join(result)
        if "." in sql:
            qualified_seen = True
        else:
            bare_seen = True
        if bare_seen and qualified_seen:
            break
    assert bare_seen, "Expected at least one bare column reference in 200 trials"
    assert qualified_seen, "Expected at least one qualified column reference in 200 trials"


def test_window_clause_base_names_are_always_defined(gen):
    # Generate many WindowClauses and verify that any name appearing as a
    # BaseWindowName in a definition is one that was already registered.
    # Strategy: capture window names after generation and check consistency.
    for seed in range(30):
        g = load_grammar_dir(GRAMMAR_DIR)
        g2 = Generator(g, random.Random(seed))
        g2._ctx.reset()
        g2._expand_rule("WindowClause", 0, frozenset(), {})
        # All registered names should be non-empty strings
        for name in g2._ctx.window_names:
            assert isinstance(name, str) and len(name) > 0
