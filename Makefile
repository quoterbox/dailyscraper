.PHONY: sync test test-network lint format headers licenses check

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

headers:
	uv run python tools/license_headers.py --fix

licenses:
	uv run python tools/third_party_licenses.py --write

check: lint test
