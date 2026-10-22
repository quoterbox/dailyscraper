from __future__ import annotations

import argparse
import subprocess
import sys

SUITES = {
    "fast": ["tests/unit", "tests/integration"],
    "network": ["tests/acceptance/controlled_site", "-m", "network"],
    "browser": ["tests/acceptance/controlled_site", "-m", "browser"],
    "full": [
        "tests/unit",
        "tests/integration",
        "tests/acceptance",
        "-m",
        "not soak and not external",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one documented pytest profile.")
    parser.add_argument("suite", choices=tuple(SUITES))
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Additional pytest arguments (prefix with --).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = [sys.executable, "-m", "pytest", "-q", *SUITES[args.suite], *args.pytest_args]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
