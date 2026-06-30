"""conftest.py — pytest path configuration for the StellarFlow ingestion suite.

Adds ``src/`` to ``sys.path`` so that ``import ingestion`` resolves when pytest
is invoked from the project root (e.g. ``python -m pytest tests/``).
"""
import sys
from pathlib import Path

# Insert src/ at the front of sys.path once, idempotently.
_SRC = str(Path(__file__).parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
