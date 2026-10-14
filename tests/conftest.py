import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def gen():
    """Import generator/generate_meter_reads.py as a module."""
    path = ROOT / "generator" / "generate_meter_reads.py"
    spec = importlib.util.spec_from_file_location("generate_meter_reads", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_meter_reads"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def db_available():
    """Integration tests only run when a database is configured (CI sets these)."""
    if not os.getenv("POSTGRES_HOST"):
        pytest.skip("POSTGRES_HOST not set: skipping database tests")
