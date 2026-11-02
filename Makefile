.PHONY: help install test lint plugin clean

help:
	@echo "install  - editable install with dev extras"
	@echo "test     - run the dependency-free test suite"
	@echo "lint     - ruff check src tests"
	@echo "plugin   - build the Obsidian plugin"
	@echo "clean    - remove build artefacts"

install:
	python -m pip install -e ".[dev]"

test:
	python tests/run_tests.py

lint:
	ruff check src tests

plugin:
	cd obsidian-litradar && pnpm install && pnpm run build

clean:
	rm -rf build dist *.egg-info .ruff_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf obsidian-litradar/main.js obsidian-litradar/node_modules
