.PHONY: sync test test-network lint format check

sync:
	uv sync

test:
	uv run python tools/test_suite.py fast

test-network:
	uv run python tools/test_suite.py network

lint:
	uv run ruff check .

format:
	uv run ruff format .

check: lint test
