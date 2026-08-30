"""Tests for the small runtime capability registry."""

from pathlib import Path
import sys

import pytest

from src.capabilities import CapabilityConfigError, load_capabilities, run_capability


def _write_catalog(path: Path, safety: str = "reversible") -> None:
    executable = sys.executable.replace("\\", "\\\\")
    path.write_text(
        f"""
probe:
  description: Test capability
  safety: {safety}
  tools:
    - name: python
      argv: [\"{executable}\", \"-c\", \"print('runtime-ok')\"]
      check_argv: [\"{executable}\", \"--version\"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_catalog_loads_explicit_safety(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "approval-required")
    capability = load_capabilities(catalog)["probe"]
    assert capability.safety == "approval-required"
    assert capability.tools[0].argv[0]


def test_invalid_safety_fails_closed(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "totally-safe-trust-me")
    with pytest.raises(CapabilityConfigError):
        load_capabilities(catalog)


def test_plan_does_not_execute(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog)
    result = run_capability("probe", execute=False, path=catalog)
    assert result["status"] == "planned"
    assert "stdout" not in result


def test_approval_capability_needs_acknowledgement(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "approval-required")
    blocked = run_capability("probe", execute=True, user_approved=False, path=catalog)
    assert blocked["status"] == "approval_required"

    completed = run_capability("probe", execute=True, user_approved=True, path=catalog)
    assert completed["status"] == "completed"
    assert "runtime-ok" in completed["stdout"]


def test_forbidden_capability_never_executes(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "forbidden")
    result = run_capability("probe", execute=True, user_approved=True, path=catalog)
    assert result["status"] == "blocked"
    assert "stdout" not in result
