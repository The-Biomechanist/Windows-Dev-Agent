"""Windows-native file metadata contracts."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from src.windows_metadata import windows_file_version


@pytest.mark.skipif(os.name != "nt", reason="Windows version resources")
def test_python_executable_has_native_file_version():
    version = windows_file_version(sys.executable)
    assert version is not None
    parts = version.split(".")
    assert len(parts) == 4
    assert all(part.isdigit() for part in parts)


def test_nonabsolute_path_is_not_metadata_authority():
    assert windows_file_version(Path("python.exe")) is None
