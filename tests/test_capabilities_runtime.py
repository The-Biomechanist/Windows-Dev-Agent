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


def _reviewed_executable(catalog: Path) -> str:
    return run_capability("probe", execute=False, path=catalog)["executable"]


def test_catalog_loads_only_live_execution_fields(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "approval-required")
    capability = load_capabilities(catalog)["probe"]
    assert capability.safety == "approval-required"
    assert capability.tools[0].argv[0]
    assert not hasattr(capability.tools[0], "verify_argv")
    assert not hasattr(capability.tools[0], "rollback_argv")


def test_invalid_safety_fails_closed(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "totally-safe-trust-me")
    with pytest.raises(CapabilityConfigError):
        load_capabilities(catalog)


def test_non_json_catalog_is_rejected_without_optional_parser(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    catalog.write_text("probe:\n  description: ordinary-yaml\n", encoding="utf-8")
    with pytest.raises(CapabilityConfigError, match="valid JSON"):
        load_capabilities(catalog)


def test_plan_resolves_executable_identity_without_executing(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog)
    result = run_capability("probe", execute=False, path=catalog)
    assert result["status"] == "planned"
    assert result["safety_class"] == "reversible"
    assert result["requires_host_approval"] is True
    assert result["executable"] == result["argv"][0]
    assert Path(result["argv"][0]).is_absolute()
    assert "stdout" not in result


def test_execute_request_has_no_model_supplied_approval_bit(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "approval-required")
    expected = _reviewed_executable(catalog)
    result = run_capability("probe", execute=True, expected_executable=expected, path=catalog)
    assert result["status"] == "completed"
    assert result["execution_started"] is True
    assert "runtime-ok" in result["stdout"]


def test_execute_requires_reviewed_executable_identity(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "read-only")
    monkeypatch.setattr(capabilities, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")))

    missing = run_capability("probe", execute=True, path=catalog)
    assert missing["status"] == "invalid_input"
    assert missing["execution_started"] is False

    stale = run_capability("probe", execute=True, expected_executable=str(tmp_path / "other.exe"), path=catalog)
    assert stale["status"] == "stale_plan"
    assert stale["execution_started"] is False


def test_extra_args_upgrade_effective_safety(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "reversible")
    capability = load_capabilities(catalog)["probe"]
    assert effective_safety(capability, []) == "reversible"
    assert effective_safety(capability, ["--fix"]) == "approval-required"
    plan = run_capability("probe", execute=False, extra_args=["--fix"], path=catalog)
    assert plan["base_safety_class"] == "reversible"
    assert plan["safety_class"] == "approval-required"
    assert plan["requires_host_approval"] is True


def test_read_only_capability_with_extra_args_is_not_silently_read_only(tmp_path: Path):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "read-only")
    plan = run_capability("probe", execute=False, extra_args=["--anything"], path=catalog)
    assert plan["base_safety_class"] == "read-only"
    assert plan["safety_class"] == "approval-required"


def test_runner_receives_exact_resolved_executable(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "read-only")
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return {
            "succeeded": True,
            "returncode": 0,
            "stdout": "runtime-ok\n",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        }

    monkeypatch.setattr(capabilities, "run_bounded", fake_run)
    expected = _reviewed_executable(catalog)
    result = run_capability("probe", execute=True, expected_executable=expected, path=catalog)
    assert result["status"] == "completed"
    assert Path(observed["argv"][0]).is_absolute()
    assert Path(observed["argv"][0]).resolve() == Path(sys.executable).resolve()


def test_spawn_failure_is_not_reported_as_executed(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "read-only")
    monkeypatch.setattr(
        capabilities,
        "run_bounded",
        lambda *_args, **_kwargs: {"succeeded": False, "error": "launch failed", "execution_started": False},
    )
    result = run_capability("probe", execute=True, expected_executable=_reviewed_executable(catalog), path=catalog)
    assert result["status"] == "failed"
    assert result["execution_started"] is False
    assert "timed_out" not in result


def test_timeout_preserves_started_but_unfinished_execution_state(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "read-only")
    monkeypatch.setattr(
        capabilities,
        "run_bounded",
        lambda *_args, **_kwargs: {"succeeded": False, "error": "timeout", "execution_started": True, "timed_out": True},
    )
    result = run_capability("probe", execute=True, expected_executable=_reviewed_executable(catalog), path=catalog)
    assert result["status"] == "failed"
    assert result["execution_started"] is True
    assert result["timed_out"] is True


def test_forbidden_capability_never_executes(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "capabilities.json"
    _write_catalog(catalog, "forbidden")
    monkeypatch.setattr(capabilities, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")))
    result = run_capability("probe", execute=True, path=catalog)
    assert result["status"] == "blocked"
    assert result["execution_started"] is False
    assert "stdout" not in result
