"""Contracts for incident response: evidence, plan validation, execution, and undo.

Every test injects a fake host, so none of these tests reaches PowerShell.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.incident import engine, evidence, model
from src.incident.engine import OpError, OpTimeout
from src.safety import gate

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
UPDATER = "C:\\Users\\me\\AppData\\Local\\Temp\\updater.exe"
UPDATER_STARTED = "2026-10-24T11:50:00.0000000Z"


def _snapshot(**changes):
    snapshot = {
        "schema": 1,
        "collected_at": NOW.isoformat(),
        "processes": [
            {
                "pid": 4242, "name": "updater", "path": UPDATER, "started": UPDATER_STARTED,
                "signed": False, "signer": None, "connections": 2, "remote": ["203.0.113.7"],
            },
            {
                "pid": 900, "name": "explorer", "path": "C:\\Windows\\explorer.exe",
                "started": "2026-10-24T08:00:00.0000000Z", "signed": True,
                "signer": "CN=Microsoft Windows", "connections": 0, "remote": [],
            },
        ],
        "persistence": [
            {
                "kind": "run_key", "hive": "HKCU", "name": "Updater", "location": "HKCU Run",
                "command": UPDATER, "path": UPDATER, "signed": False, "signer": None,
            },
            {
                "kind": "scheduled_task", "name": "UpdateCheck", "task_path": "\\", "location": "\\",
                "command": UPDATER, "path": UPDATER, "signed": False, "signer": None,
            },
        ],
        "defender": {"realtime_protection": True, "recent_threats": []},
        "proxy": {"enabled": False, "server": None},
        "hosts": {"non_default_entries": 0, "sample": []},
        "warnings": [],
    }
    snapshot.update(changes)
    return snapshot


class FakeHost:
    """Records every operation and returns scripted results or failures."""

    def __init__(self, snapshot=None, fail=None, results=None):
        self.snapshot = snapshot if snapshot is not None else _snapshot()
        self.fail = fail or {}
        self.results = results or {}
        self.calls = []

    def __call__(self, op, payload, timeout):
        self.calls.append((op, payload))
        if op in self.fail:
            raise self.fail[op]
        if op == "collect":
            return self.snapshot
        return self.results.get(op, {})


def _collect(tmp_path, host=None):
    host = host or FakeHost()
    result = engine.collect(tmp_path, runner=host, now=NOW)
    assert result["status"] == "ok", result
    return result["evidence_id"], host


def _plan(evidence_id, steps, goal="contain the updater"):
    return {"version": 1, "evidence_id": evidence_id, "goal": goal, "steps": steps}


def _step(step_id, action, target, reason="observed in evidence"):
    return {"id": step_id, "action": action, "target": target, "reason": reason}


QUARANTINE = _step("s1", "quarantine_file", {"path": UPDATER})
STOP = _step("s2", "stop_process", {"pid": 4242, "path": UPDATER, "name": "updater", "started": UPDATER_STARTED})
DISABLE_RUN = _step("s3", "disable_run_value", {"hive": "HKCU", "name": "Updater"})


def _errors(plan, snapshot=None):
    record = {"evidence_id": "0123456789abcdef", "collected_at": NOW.isoformat(), "snapshot": snapshot or _snapshot()}
    return model.validate_plan(plan, record, NOW)


# --- evidence scoring -------------------------------------------------------

def test_clean_machine_produces_no_indicators():
    clean = _snapshot(
        processes=[_snapshot()["processes"][1]],
        persistence=[],
    )
    assert evidence.indicators(clean) == []


def test_unsigned_network_program_in_user_folder_is_high():
    found = {item["id"]: item for item in evidence.indicators(_snapshot())}
    assert found["unsigned_program_in_user_folder"]["severity"] == "high"
    assert "not proof of malware" in found["unsigned_program_in_user_folder"]["explanation"]


def test_unsigned_autostart_from_user_folder_is_high():
    found = {item["id"]: item for item in evidence.indicators(_snapshot())}
    assert found["unsigned_autostart_from_user_folder"]["severity"] == "high"


def test_defender_realtime_off_and_proxy_are_reported():
    snapshot = _snapshot(
        defender={"realtime_protection": False, "recent_threats": []},
        proxy={"enabled": True, "server": "203.0.113.9:8080"},
    )
    ids = {item["id"] for item in evidence.indicators(snapshot)}
    assert {"defender_realtime_off", "system_proxy_enabled"} <= ids


def test_host_strings_are_sanitized_before_display():
    hostile = _snapshot(defender={"realtime_protection": True, "recent_threats": [{"name": "x" * 500 + "\x07"}]})
    found = [item for item in evidence.indicators(hostile) if item["id"] == "recent_defender_detections"]
    assert found and "\x07" not in found[0]["explanation"] and len(found[0]["explanation"]) < 400


# --- plan validation --------------------------------------------------------

def test_plan_against_observed_targets_is_valid():
    assert _errors(_plan("0123456789abcdef", [QUARANTINE, STOP, DISABLE_RUN])) == []


def test_unknown_action_is_refused_by_name():
    bad = _step("s1", "delete_file", {"path": UPDATER})
    assert any("not allowed" in e for e in _errors(_plan("0123456789abcdef", [bad])))


def test_target_not_in_latest_evidence_is_refused():
    step = _step("s1", "quarantine_file", {"path": "C:\\Users\\me\\Documents\\notes.txt"})
    assert any("not observed" in e for e in _errors(_plan("0123456789abcdef", [step])))


def test_protected_locations_are_refused_even_if_listed():
    step = _step("s1", "quarantine_file", {"path": "C:\\Windows\\System32\\evil.dll"})
    assert any("protected" in e for e in _errors(_plan("0123456789abcdef", [step])))


def test_path_traversal_is_refused():
    step = _step("s1", "quarantine_file", {"path": "C:\\Users\\me\\..\\..\\Windows\\x.exe"})
    assert any("'..'" in e for e in _errors(_plan("0123456789abcdef", [step])))


def test_protected_system_process_is_refused_even_when_observed():
    snapshot = _snapshot(processes=[{
        "pid": 700, "name": "lsass", "path": "C:\\Windows\\System32\\lsass.exe",
        "started": UPDATER_STARTED, "signed": True, "signer": "CN=Microsoft", "connections": 0, "remote": [],
    }])
    step = _step("s1", "stop_process", {"pid": 700, "path": "C:\\Windows\\System32\\lsass.exe", "name": "lsass", "started": UPDATER_STARTED})
    assert any("protected" in e for e in _errors(_plan("0123456789abcdef", [step]), snapshot))


def test_stop_requires_matching_pid_and_start_time():
    reused = _step("s1", "stop_process", {"pid": 4242, "path": UPDATER, "name": "updater", "started": "2026-10-24T11:59:00.0000000Z"})
    assert any("pid, name, path, and start time" in e for e in _errors(_plan("0123456789abcdef", [reused])))


def test_private_and_loopback_addresses_are_never_blocked():
    snapshot = _snapshot(processes=[{
        "pid": 5, "name": "router-chat", "path": UPDATER, "started": UPDATER_STARTED,
        "signed": False, "signer": None, "connections": 1, "remote": ["192.168.1.1", "8.8.8.8"],
    }])
    private = _step("s1", "block_remote_address", {"address": "192.168.1.1"})
    public = _step("s2", "block_remote_address", {"address": "8.8.8.8"})
    errors = _errors(_plan("0123456789abcdef", [private, public]), snapshot)
    assert any("never blocked" in e for e in errors)
    assert not any("step 2" in e for e in errors)


def test_signed_items_are_never_acted_on():
    signed_app = "C:\\Users\\me\\AppData\\Local\\Programs\\App\\app.exe"
    snapshot = _snapshot(processes=[{
        "pid": 77, "name": "app", "path": signed_app, "started": UPDATER_STARTED,
        "signed": True, "signer": "CN=Example Software", "connections": 1, "remote": ["8.8.8.8"],
    }], persistence=[])
    quarantine = _step("s1", "quarantine_file", {"path": signed_app})
    stop = _step("s2", "stop_process", {"pid": 77, "path": signed_app, "name": "app", "started": UPDATER_STARTED})
    block = _step("s3", "block_program_outbound", {"path": signed_app})
    errors = _errors(_plan("0123456789abcdef", [quarantine, stop, block]), snapshot)
    assert sum("trusted publisher" in e for e in errors) == 3


def test_built_in_windows_tasks_are_never_disabled():
    snapshot = _snapshot(persistence=[{
        "kind": "scheduled_task", "name": "Defrag", "task_path": "\\Microsoft\\Windows\\Defrag\\",
        "location": "\\Microsoft\\Windows\\Defrag\\", "command": UPDATER, "path": UPDATER,
        "signed": False, "signer": None,
    }])
    step = _step("s1", "disable_scheduled_task", {"task_path": "\\Microsoft\\Windows\\Defrag\\", "task_name": "Defrag"})
    assert any("never disabled" in e for e in _errors(_plan("0123456789abcdef", [step]), snapshot))


def test_run_value_must_be_an_observed_startup_entry():
    step = _step("s1", "disable_run_value", {"hive": "HKLM", "name": "Updater"})
    assert any("startup value was not observed" in e for e in _errors(_plan("0123456789abcdef", [step])))


def test_step_count_and_stale_evidence_are_refused():
    many = [_step(f"s{i}", "quarantine_file", {"path": UPDATER}) for i in range(13)]
    assert any("1 to 12 steps" in e for e in _errors(_plan("0123456789abcdef", many)))
    stale = {"evidence_id": "0123456789abcdef", "collected_at": (NOW - timedelta(minutes=31)).isoformat(), "snapshot": _snapshot()}
    assert any("older than 30 minutes" in e for e in model.validate_plan(_plan("0123456789abcdef", [QUARANTINE]), stale, NOW))


def test_unknown_fields_are_refused_in_plan_and_steps():
    plan = _plan("0123456789abcdef", [{**QUARANTINE, "auto_approved": True}])
    plan["approved_by_model"] = True
    errors = _errors(plan)
    assert any("unknown plan field" in e for e in errors)
    assert any("unknown field" in e for e in errors)


# --- execution --------------------------------------------------------------

def test_dry_run_validates_and_never_touches_the_host(tmp_path):
    evidence_id, host = _collect(tmp_path)
    result = engine.execute(_plan(evidence_id, [QUARANTINE]), tmp_path, execute=False, now=NOW, runner=host)
    assert result["status"] == "planned"
    assert result["steps"] and "Undo:" in result["steps"][0]
    assert [op for op, _ in host.calls] == ["collect"]


def test_digest_is_stable_across_key_order_and_detects_changes(tmp_path):
    evidence_id, host = _collect(tmp_path)
    plan = _plan(evidence_id, [QUARANTINE])
    reordered = dict(reversed(list(plan.items())))
    assert model.plan_digest(plan) == model.plan_digest(reordered)
    refused = engine.execute(plan, tmp_path, execute=True, expected_digest="0" * 64, runner=host, now=NOW)
    assert refused["status"] == "invalid_input"
    assert any("changed after it was reviewed" in e for e in refused["errors"])
    assert all(op == "collect" for op, _ in host.calls)


def test_execution_writes_a_manifest_and_stops_at_first_failure(tmp_path):
    evidence_id, _ = _collect(tmp_path)
    host = FakeHost(
        results={"quarantine_file": {"original_path": UPDATER, "quarantined_path": "q\\01-updater.exe", "sha256": "ab"}},
        fail={"stop_process": OpError("access denied")},
    )
    result = engine.execute(_plan(evidence_id, [QUARANTINE, STOP, DISABLE_RUN]), tmp_path, execute=True, runner=host, now=NOW)
    assert result["status"] == "stopped_failed"
    assert result["execution_started"] is True
    assert [op for op, _ in host.calls] == ["quarantine_file", "stop_process"]
    statuses = [s["status"] for s in result["steps"]]
    assert statuses == ["done", "failed", "pending"]
    manifest = json.loads(
        (tmp_path / "incident" / "executions" / result["execution_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "stopped_failed"


def test_timeout_is_recorded_as_unknown_not_failed(tmp_path):
    evidence_id, _ = _collect(tmp_path)
    host = FakeHost(fail={"quarantine_file": OpTimeout("timed out")})
    result = engine.execute(_plan(evidence_id, [QUARANTINE]), tmp_path, execute=True, runner=host, now=NOW)
    assert result["status"] == "stopped_unknown"
    assert result["steps"][0]["status"] == "unknown"


def test_plan_refers_only_to_evidence_on_record(tmp_path):
    result = engine.execute(_plan("ffffffffffffffff", [QUARANTINE]), tmp_path, execute=False, now=NOW)
    assert result["status"] == "invalid_input"
    assert "run incident_collect first" in result["errors"][0]


# --- undo -------------------------------------------------------------------

def test_undo_reverses_completed_steps_newest_first(tmp_path):
    evidence_id, _ = _collect(tmp_path)
    results = {
        "quarantine_file": {"original_path": UPDATER, "quarantined_path": "q\\01-updater.exe", "sha256": "ab"},
        "disable_run_value": {"hive": "HKCU", "name": "Updater", "value": UPDATER},
    }
    host = FakeHost(results=results)
    plan = _plan(evidence_id, [QUARANTINE, DISABLE_RUN])
    applied = engine.execute(plan, tmp_path, execute=True, runner=host, now=NOW)
    assert applied["status"] == "completed"

    undo_host = FakeHost()
    preview = engine.undo(applied["execution_id"], tmp_path, execute=False, runner=undo_host)
    assert [s["action"] for s in preview["steps"]] == ["disable_run_value", "quarantine_file"]
    assert undo_host.calls == []

    reversed_result = engine.undo(applied["execution_id"], tmp_path, execute=True, runner=undo_host)
    assert reversed_result["status"] == "undone"
    assert [op for op, _ in undo_host.calls] == ["undo_disable_run_value", "undo_quarantine_file"]
    assert undo_host.calls[0][1] == {"hive": "HKCU", "name": "Updater", "value": UPDATER}
    assert engine.undo(applied["execution_id"], tmp_path, execute=True, runner=undo_host)["status"] == "invalid_input"


def test_undo_failure_is_reported_as_partial(tmp_path):
    evidence_id, _ = _collect(tmp_path)
    host = FakeHost(results={"quarantine_file": {"original_path": UPDATER, "quarantined_path": "q", "sha256": "ab"}})
    applied = engine.execute(_plan(evidence_id, [QUARANTINE]), tmp_path, execute=True, runner=host, now=NOW)
    undo_host = FakeHost(fail={"undo_quarantine_file": OpError("original location is occupied")})
    result = engine.undo(applied["execution_id"], tmp_path, execute=True, runner=undo_host)
    assert result["status"] == "undo_partial"
    assert result["steps"][0]["undo_status"] == "undo_failed"


def test_malformed_identifiers_never_reach_the_filesystem(tmp_path):
    assert engine.load_evidence(tmp_path, "../../etc/passwd") is None
    assert engine.undo("../../x", tmp_path, execute=True)["status"] == "invalid_input"


def test_collect_reports_unavailable_when_the_host_cannot_run(tmp_path):
    host = FakeHost(fail={"collect": OpError("Windows PowerShell was not found")})
    result = engine.collect(tmp_path, runner=host, now=NOW)
    assert result["status"] == "unavailable"


def test_summary_names_the_plan_for_the_approval_prompt():
    plan = _plan("0123456789abcdef", [QUARANTINE, STOP])
    summary = model.summarize_request({"plan": plan})
    assert summary.startswith("Plan ") and "2 step(s)" in summary


# --- gate -------------------------------------------------------------------

def _decision(tool_name, tool_input):
    output = gate.evaluate_hook_event({"tool_name": tool_name, "tool_input": tool_input}, log_file=None)
    return None if output is None else output["hookSpecificOutput"]["permissionDecision"]


INCIDENT = "mcp__plugin_windows-dev-agent_windows-dev-agent__"


def test_evidence_collection_is_the_only_autonomous_allow():
    assert _decision(INCIDENT + "incident_collect", {}) == "allow"


def test_planning_dry_run_defers_and_execution_asks_with_the_plan_summary():
    assert _decision(INCIDENT + "incident_execute", {"plan": _plan("0123456789abcdef", [QUARANTINE])}) is None
    output = gate.evaluate_hook_event(
        {"tool_name": INCIDENT + "incident_execute", "tool_input": {"plan": _plan("0123456789abcdef", [QUARANTINE]), "execute": True}},
        log_file=None,
    )
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "Plan " in decision["permissionDecisionReason"]


def test_undo_asks_before_reversing_anything():
    assert _decision(INCIDENT + "incident_undo", {"execution_id": "0" * 16}) is None
    assert _decision(INCIDENT + "incident_undo", {"execution_id": "0" * 16, "execute": True}) == "ask"


def test_only_evidence_collection_can_be_allowed_by_the_gate():
    for name in ("incident_execute", "incident_undo"):
        for tool_input in ({}, {"execute": True}):
            assert _decision(INCIDENT + name, tool_input) != "allow"


@pytest.mark.parametrize("name", ["incident_collect", "incident_execute", "incident_undo"])
def test_incident_tools_are_in_the_claude_hook_matcher(name):
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    matcher = re.compile(config["hooks"]["PreToolUse"][0]["matcher"])
    assert matcher.search(INCIDENT + name)
