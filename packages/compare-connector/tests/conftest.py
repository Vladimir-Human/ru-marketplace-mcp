"""Marks this directory as its own pytest rootdir package.

Several connectors have a ``test_server.py``; without a conftest per directory
pytest cannot tell the identically-named modules apart during collection.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The benchmark folder holds the recording stub next to its test, and pytest's
# importlib mode does not put a test directory on sys.path.
BENCHMARK = Path(__file__).resolve().parent / "benchmark"
if str(BENCHMARK) not in sys.path:
    sys.path.insert(0, str(BENCHMARK))
