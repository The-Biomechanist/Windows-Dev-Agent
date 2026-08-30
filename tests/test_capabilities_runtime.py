"""Contract tests for the executable capability registry."""

import json
from pathlib import Path
import sys

import pytest

from src import capabilities
from src.capabilities import CapabilityConfigError, effective_safety, load_capabilities, run_capability


def _write_catalog(path: Path, safety: str = "reversible") -> None:
    path.write_text(
        json.dumps(
            {
                "probe": {
                    "description": "Test capability",
                    "safety": safety,
                    "tools": [{"name": "python", "argv": [sys.executable, "-c", "print('runtime-ok')"]}],
                }
            }
        ),
        encoding="utf-8",
    )


def test_catalog_loads_only_live_execution_fields(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "approval-required")
    capability = load_capabilities(catalog)["probe"]
    assert capability.safety == "approval-required"
    assert capability.tools[0].argv[0]
    assert not hasattr(capability.tools[0], "verify_argv")
    assert not hasattr(capability.tools[0], "rollback_argv")


def test_invalid_safety_fails_closed(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "totally-safe-trust-me")
    with pytest.raises(CapabilityConfigError):
        load_capabilities(catalog)


def test_non_json_yaml_extension_is_rejected_without_optional_parser(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    catalog.write_text("probe:\n  description: ordinary-yaml\n", encoding="utf-8")
    with pytest.raises(CapabilityConfigError, match="JSON-compatible YAML"):
        load_capabilities(catalog)


def test_plan_does_not_execute(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog)
    result = run_capability("probe", execute=False, path=catalog)
    assert result["status"] == "planned"
    assert result["safety_class"] == "reversible"
    assert result["requires_host_approval"] is True
    assert "stdout" not in result


def test_execute_request_has_no_model_supplied_approval_bit(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "approval-required")
    result = run_capability("probe", execute=True, path=catalog)
    assert result["status"] == "completed"
    assert result["execution_started"] is True
    assert "runtime-ok" in result["stdout"]


def test_extra_args_upgrade_effective_safety(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "reversible")
    capability = load_capabilities(catalog)["probe"]
    assert effective_safety(capability, []) == "reversible"
    assert effective_safety(capability, ["--fix"]) == "approval-required"
    plan = run_capability("probe", execute=False, extra_args=["--fix"], path=catalog)
    assert plan["base_safety_class"] == "reversible"
    assert plan["safety_class"] == "approval-required"
    assert plan["requires_host_approval"] is True


def test_read_only_capability_with_extra_args_is_not_silently_read_only(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "read-only")
    plan = run_capability("probe", execute=False, extra_args=["--anything"], path=catalog)
    assert plan["base_safety_class"] == "read-only"
    assert plan["safety_class"] == "approval-required"


def test_capability_subprocess_cannot_consume_mcp_stdin(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "read-only")
    observed = {}

    class Result:
        returncode = 0
        stdout = "runtime-ok\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return Result()

    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    result = run_capability("probe", execute=True, path=catalog)
    assert result["status"] == "completed"
    assert observed["stdin"] is capabilities.subprocess.DEVNULL


def test_forbidden_capability_never_executes(tmp_path: Path):
    catalog = tmp_path / "capabilities.yaml"
    _write_catalog(catalog, "forbidden")
    result = run_capability("probe", execute=True, path=catalog)
    assert result["status"] == "blocked"
    assert "stdout" not in result
