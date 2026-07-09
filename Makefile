# This is used with `make <option>` and is used for running various
# administration operations on the code.

TEST_GAME_DIR = .test_game_dir
TESTS ?= evennia

default:
	@echo " Usage: "
	@echo "  make install - install evennia (recommended to activate virtualenv first)"
	@echo "  make installextra - install evennia with extra-requirements (activate virtualenv first)"
	@echo "  make fmt/format - run the ruff formatter + import sort on the source code"
	@echo "  make lint - run ruff format --check + import-sort check"
	@echo "  make test - run evennia test suite with all default values."
	@echo "  make tests=evennia.path test - run only specific test or tests."
	@echo "  make testp - run test suite using multiple cores."
	@echo "  make release - publish evennia to pypi (requires pypi credentials)"
	@echo "  make cleanrot - check agent context docs for rot/bloat"

install:
	pip install -e .

installextra:
	pip install -e .
	pip install -e .[extra]

# ruff is configured from pyproject.toml ([tool.ruff]); run via `uv run` if it
# is not on PATH.
format:
	ruff format .
	ruff check --select I --fix .

fmt: format

lint:
	ruff format --check .
	ruff check --select I .

test:
	evennia --init $(TEST_GAME_DIR);\
	cd $(TEST_GAME_DIR);\
	evennia migrate;\
	evennia test --keepdb $(TESTS);\

testp:
	evennia --init $(TEST_GAME_DIR);\
	cd $(TEST_GAME_DIR);\
	evennia migrate;\
	evennia test --keepdb --parallel 4 $(TESTS);\

version:
	echo $(VERSION)

release:
	./.release.sh

cleanrot:
	python .agents/tools/clean_rot.py
