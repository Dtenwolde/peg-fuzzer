VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

GRAMMAR_DIR := duckdb/extension/autocomplete/grammar

.PHONY: venv install test run verify verify-rebuild gen-tests clean

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install -e ".[dev]"

test: install
	$(PYTEST) tests/ -v

run: install
	$(PYTHON) -m peg_fuzzer --grammar-dir $(GRAMMAR_DIR) $(ARGS)

# Build DuckDB from the submodule and re-run every interesting/ SQL file.
# Optional: BRANCH=my-branch make verify
verify: install
	$(PYTHON) scripts/verify_interesting.py $(if $(BRANCH),--branch $(BRANCH),) $(ARGS)

# Force a rebuild even if the binary already exists.
verify-rebuild: install
	$(PYTHON) scripts/verify_interesting.py --rebuild $(if $(BRANCH),--branch $(BRANCH),) $(ARGS)

# Regenerate fuzzer_issues/ .test files from interesting/diverge_*.sql
gen-tests: install
	$(PYTHON) scripts/gen_duckdb_tests.py $(ARGS)

clean:
	rm -rf $(VENV) __pycache__ peg_fuzzer/__pycache__ tests/__pycache__ \
	       *.egg-info dist build crashes interesting
