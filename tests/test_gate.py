"""Contracts for the Claude Code tightening-only safety gate."""

from src.safety import gate


def _decision(tool_name, tool_input):
    output = gate.evaluate_hook_event({"tool_name": tool_name, "tool_input": tool_input}, log_file=None)
    return None if output is None else output["hookSpecificOutput"]["permissionDecision"]


def test_read_only_shell_defers_to_host():
    assert gate.classify_bash("git status --short") == "read-only"
    assert _decision("Bash", {"command": "git status --short"}) is None
    assert gate.classify_shell("Get-ChildItem C:\\src") == "read-only"
    assert _decision("PowerShell", {"command": "Get-ChildItem C:\\src"}) is None


def test_unknown_mutation_and_optional_feature_change_require_host_permission():
    assert _decision("Bash", {"command": "some-new-tool mutate-things"}) == "ask"
    command = "Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux"
    assert _decision("PowerShell", {"command": command}) == "ask"


def test_compound_redirected_and_dynamic_commands_cannot_inherit_read_only():
    for command in (
        "git status --short; some-new-tool mutate-things",
        "Get-ChildItem | Set-Content out.txt",
        "git status --short > status.txt",
        "git status $(touch changed.txt)",
        "Get-ChildItem & some-command",
    ):
        assert gate.classify_shell(command) == "approval-required"


def test_reversible_project_code_is_not_autoallowed():
    assert gate.classify_shell("pytest") == "reversible"
    assert _decision("Bash", {"command": "pytest"}) is None
    assert _decision("PowerShell", {"command": "dotnet build"}) is None


def test_destructive_disk_and_system32_commands_are_forbidden():
    assert gate.classify_shell("format C:") == "forbidden"
    assert _decision("PowerShell", {"command": "format C:"}) == "deny"
    system32 = r"Remove-Item C:\Windows\System32\drivers\example.sys"
    assert gate.classify_shell(system32) == "forbidden"
    assert _decision("PowerShell", {"command": system32}) == "deny"


def test_package_plan_defers_but_execute_asks_without_fake_approval_bit(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    tool = "mcp__plugin_windows-dev-agent_windows-dev-agent__package_install"
    assert gate.evaluate_hook_event({"tool_name": tool, "tool_input": {"package_id": "Python.Python.3.12", "execute": False}}) is None
    output = gate.evaluate_hook_event({"tool_name": tool, "tool_input": {"package_id": "Python.Python.3.12", "execute": True}})
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_capability_effective_authority_uses_base_and_extra_args(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    tool = "mcp__plugin_windows-dev-agent_windows-dev-agent__capability_run"
    reversible = {"capability": "lint-python", "execute": True}
    assert gate.classify_tool_call(tool, reversible) == "reversible"
    assert gate.evaluate_hook_event({"tool_name": tool, "tool_input": reversible}) is None
    extra = {"capability": "lint-python", "extra_args": ["--fix"], "execute": True}
    assert gate.classify_tool_call(tool, extra) == "approval-required"
    assert gate.evaluate_hook_event({"tool_name": tool, "tool_input": extra})["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_project_only_ecosystem_read_defers_but_host_scope_asks():
    tool = "mcp__plugin_windows-dev-agent_windows-dev-agent__ecosystem_scan"
    assert gate.classify_tool_call(tool, {"include_host": False}) == "read-only"
    assert _decision(tool, {"include_host": False}) is None
    assert gate.classify_tool_call(tool, {"include_host": True}) == "approval-required"
    assert _decision(tool, {"include_host": True}) == "ask"


def test_project_mcp_audit_defers_but_host_or_arbitrary_file_reads_ask():
    tool = "mcp__plugin_windows-dev-agent_windows-dev-agent__mcp_audit"
    assert _decision(tool, {"include_host": False}) is None
    assert _decision(tool, {"include_host": True}) == "ask"
    assert _decision(tool, {"config_path": "C:\\other\\mcp.json"}) == "ask"
