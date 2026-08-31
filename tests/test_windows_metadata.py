"""Windows-native file metadata contracts."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from src.windows_metadata import windows_file_version


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(os.name != "nt", reason="Windows version resources")
def test_python_executable_has_native_file_version():
    version = windows_file_version(sys.executable)
    assert version is not None
    parts = version.split(".")
    assert len(parts) == 4
    assert all(part.isdigit() for part in parts)


def test_metadata_reader_is_handle_bound_not_path_reopened():
    text = (ROOT / "src" / "windows_metadata.py").read_text(encoding="utf-8")
    assert "GetFileVersionInfoByHandle" in text
    assert "GetFileVersionInfoSizeW" not in text
    assert "GetFileVersionInfoW" not in text
    assert "GetSystemDirectoryW" in text


def test_nonabsolute_path_is_not_metadata_authority():
    assert windows_file_version(Path("python.exe")) is None
