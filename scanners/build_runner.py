"""Build runner (thin module so imports match the documented architecture)."""
from __future__ import annotations

from pathlib import Path

from scanners.test_runner import run_build  # noqa: F401  (re-export)

__all__ = ["run_build"]
