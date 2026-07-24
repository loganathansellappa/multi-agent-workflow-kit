#!/usr/bin/env python3
"""Run the kit's Python test suite (stdlib unittest, zero third-party deps).

Usage: python run_tests.py
"""
import sys
import unittest
from pathlib import Path

if __name__ == "__main__":
    # Discover and run every tests/test_*.py; exit non-zero if any test fails
    # (so CI can gate on the result).
    tests_dir = Path(__file__).resolve().parent / "tests"
    suite = unittest.defaultTestLoader.discover(str(tests_dir), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
