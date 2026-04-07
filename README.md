# peg-fuzzer

A grammar-based fuzzer for DuckDB SQL. It parses the PEG grammar used by DuckDB's
autocomplete extension, generates random SQL statements from that grammar, and executes
them against both the PEG parser and the default Postgres-compatible parser to surface
crashes and behavioural divergences.

## How it works

DuckDB's autocomplete extension defines the full SQL grammar in `.gram` files under
`duckdb/extension/autocomplete/grammar/statements/`. This fuzzer:

1. Parses those `.gram` files into a grammar model (mirroring `peg_parser.cpp`).
2. Walks the grammar tree recursively to generate random SQL strings.
3. Executes each string twice -- once with the PEG parser enabled
   (`CALL enable_peg_parser()`) and once with the default Postgres parser
   (`CALL disable_peg_parser()`) -- using a fresh in-memory DuckDB connection each time.
4. Classifies each result as `OK`, `ERR` (a `duckdb.Error`), or `CRASH` (any other
   exception).
5. Reports and saves divergences (outcome differs between parsers) and crashes to the
   `interesting/` directory.

The generator respects depth limits and tracks the active rule call stack to break
recursive cycles. Rule overrides (identifiers, literals, keywords) are resolved to
concrete values directly rather than expanded through the grammar.

## Requirements

- Python 3.11+
- Git (for submodule checkout)
- No other system dependencies; DuckDB is installed as a Python package.

## Setup

```bash
git clone --recurse-submodules https://github.com/dtenwolde/peg-fuzzer
cd peg-fuzzer
make install
```

`make install` creates a `.venv` and installs the package with its dependencies.

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
make install
```

## Running

```bash
# 100 statements, random seed (default)
make run

# More statements, fixed seed for reproducibility
make run ARGS="--count 1000 --seed 42"

# Print every result, not just divergences and crashes
make run ARGS="--count 200 --verbose"

# Fuzz only SELECT statements
make run ARGS="--count 500 --start-rule SelectStatement"

# Or call the module directly after activating the venv
source .venv/bin/activate
python -m peg_fuzzer --count 1000 --start-rule SelectStatement
```

### CLI flags

| Flag | Default | Description |
|---|---|---|
| `--grammar-dir` | `duckdb/extension/autocomplete/grammar` | Path to the grammar directory |
| `--start-rule` | `Statement` | Root grammar rule to generate from |
| `--count` | `100` | Number of statements to generate and test |
| `--seed` | random | RNG seed for reproducibility |
| `--verbose` | off | Print all results, not just divergences and crashes |

### Output

Each statement is executed against both parsers. Only divergences and crashes are
printed by default:

```
[DIVERGE] PEG=OK PG=ERR  'DETACH IF EXISTS col_b'
           PG:  Parser Error: syntax error at or near "EXISTS"
           => saved interesting/diverge_0000.sql

Done: 100 queries
  PEG      -- OK=12  ERR=88  CRASH=0
  Postgres -- OK=10  ERR=90  CRASH=0
  Diverged (outcome mismatch) = 3
```

- **DIVERGE** -- the two parsers returned different outcome classes (one OK, one ERR or CRASH).
  Error message wording differences within the same outcome class are not flagged.
- **CRASH** -- a statement triggered an exception that is not a `duckdb.Error` (e.g. a
  segfault or assertion failure that bubbles up through the Python binding).

Both are saved to `interesting/` as numbered `.sql` files with a comment header:

```sql
-- DIVERGE
-- PEG:      OK
-- Postgres: ERR  Parser Error: syntax error at or near "EXISTS"

DETACH IF EXISTS col_b
```

## Testing

```bash
make test
```

Runs the test suite with pytest. Tests cover the grammar parser, the generator, and the
runner. Runner tests are parametrized over both parsers.

## Project layout

```
peg_fuzzer/
  grammar/
    model.py        -- Grammar IR: PEGToken, Rule, Grammar dataclasses
    parser.py       -- Parses .gram files; generates keyword rules from .list files
    keywords.py     -- Loads keyword frozensets (reserved, unreserved, col_name, type_func)
    overrides.py    -- Maps rule names to terminal kinds (mirrors matcher.cpp)
  generator/
    generator.py    -- Builds a Node AST per rule; walks it with depth + active-set tracking
    terminals.py    -- Generates concrete values (identifiers, numbers, strings)
  runner/
    result.py       -- RunResult, CompareResult, Outcome, Parser enums
    runner.py       -- run_one(sql, parser) and run_both(sql) -> CompareResult
  fuzzer.py         -- Main loop: generate -> run_both -> compare -> log
  cli.py            -- argparse entry point
tests/
  test_parser.py
  test_generator.py
  test_runner.py
scripts/
  verify_interesting.py  -- Build DuckDB from source and re-run interesting/ SQL files
duckdb/                  -- DuckDB submodule (grammar source of truth)
extension_config_local.cmake  -- cmake extension config: builds autocomplete into the binary
interesting/             -- Saved divergences and crashes (gitignored)
```

## Grammar format

Rules follow a PEG syntax defined in `peg_parser.cpp`:

```
RuleName <- 'KEYWORD' OtherRule? (ChoiceA / ChoiceB)* List(Arg)
```

| Syntax | Meaning |
|---|---|
| `'TEXT'` | Literal keyword (case-insensitive) |
| `OtherRule` | Reference to another rule |
| `A / B` | Ordered choice: try A first, then B |
| `X?` | Optional |
| `X*` | Zero or more |
| `X+` | One or more |
| `!X` | Negative lookahead (ignored during generation) |
| `List(D)` | Comma-separated list of D |
| `Parens(D)` | D wrapped in parentheses |

Keyword category rules (`ReservedKeyword`, `UnreservedKeyword`, `ColumnNameKeyword`,
`FuncNameKeyword`, `TypeNameKeyword`) are generated at load time from the `.list` files
in `grammar/keywords/`, matching the output of `inline_grammar.py`.

## Reproducing a result

Each file in `interesting/` is a plain `.sql` file with comment headers you can strip.
The SQL starts after the first blank line. To reproduce:

```bash
# Extract just the SQL
SQL=$(awk '/^$/{found=1; next} found' interesting/diverge_0000.sql)

# Test with PEG parser
source .venv/bin/activate
python -c "
import duckdb
conn = duckdb.connect()
conn.execute('CALL enable_peg_parser()')
conn.execute('''$SQL''')
"

# Test with Postgres parser (default)
python -c "
import duckdb
duckdb.connect().execute('''$SQL''')
"
```

## Verifying findings against a source build

The fuzzer runs against the pip-installed DuckDB. To confirm a finding against a
specific branch or commit built from the submodule, use `scripts/verify_interesting.py`.
It builds DuckDB from source (with the autocomplete extension baked in via
`extension_config_local.cmake`) and re-runs every file in `interesting/` against both
parsers using the built binary.

```bash
# Build from the current submodule HEAD and verify
make verify

# Check out a specific branch first, then build and verify
make verify BRANCH=main

# Force a rebuild (e.g. after pulling new commits)
make verify-rebuild

# Skip the build and use an existing binary
python scripts/verify_interesting.py --no-build
```

The script prints a table of outcomes per file:

```
DuckDB (built): v1.5.1-2701-g48163c9b4c

Running 20 queries from interesting/

File                                PEG      Postgres Status
--------------------------------------------------------------
diverge_0000.sql                    OK       ERR      DIVERGE
  ...                                        PG:  Parser Error: syntax error at or near "EXISTS"
diverge_0001.sql                    ERR      OK       DIVERGE
  ...                               PEG: Parser Error: Syntax error at or near "EXTENSIONS"
diverge_0002.sql                    OK       OK       same (OK)
--------------------------------------------------------------
Total: 20  Diverged: 14  Same: 6
```

A finding that was a divergence during fuzzing but shows `same` here means the
source-built version already has it fixed (or it was a version-specific behaviour).

### Build requirements

- CMake 3.21+
- A C++17 compiler (clang or gcc)
- Make

The first build takes ~10-20 minutes. Subsequent builds only recompile changed files.

## Makefile targets

| Target | Description |
|---|---|
| `make venv` | Create `.venv` only |
| `make install` | Create `.venv` and install all dependencies |
| `make test` | Run the test suite |
| `make run` | Run the fuzzer with default settings |
| `make verify` | Build DuckDB from source and re-run `interesting/` |
| `make verify-rebuild` | Same as verify but forces a full rebuild |
| `make clean` | Remove `.venv`, caches, build artifacts, and `interesting/` |
