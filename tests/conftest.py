"""Pytest configuration and fixtures."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Provide a temporary cache directory for tests."""
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep audit and environment-cache writes out of the working tree."""
    monkeypatch.setenv("WINDOWS_DEV_AGENT_DATA_DIR", str(tmp_path / "data"))
