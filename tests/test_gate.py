"""Contract tests for the Claude Code PreToolUse safety gate."""

from src.safety import gate


def _decision(tool_name, tool_input):
    output = gate.evaluate_hook_event(
        {"tool_name": tool_name, "tool_input": tool_input},
        log_file=None,
    )
    return output["hookSpecificOutput"]["permissionDecision"]


def test_read_only_bash_is_allowed():
    assert gate.classify_bash("git status --short") == "read-only"
    assert _decision("Bash", {"command": "git status --short"}) == "allow"


def test_read_only_powershell_is_allowed():
    assert gate.classify_shell("Get-ChildItem C:\\src") == "read-only"
    assert _decision("PowerShell", {"command": "Get-ChildItem C:\\src"}) == "allow"


def test_powershell_mutation_requires_host_permission():
    command = "Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux"
    assert gate.classify_shell(command) == "approval-required"
    assert _decision("PowerShell", {"command": command}) == "ask"


def test_unknown_shell_command_asks_instead_of_default_allow():
    assert gate.classify_shell("some-new-tool mutate-things") == "approval-required"


def test_compound_command_cannot_inherit_read_only_prefix():
    assert gate.classify_shell("git status --short; some-new-tool mutate-things") == "approval-required"
    assert gate.classify_shell("Get-ChildItem | Set-Content out.txt") == "approval-required"


def test_reversible_project_code_returns_to_host_permission_path():
    assert gate.classify_shell("pytest") == "reversible"
    assert _decision("Bash", {"command": "pytest"}) == "ask"
    assert _decision("PowerShell", {"command": "dotnet build"}) == "ask"


def test_package_install_shell_requires_approval():
    assert gate.classify_shell("winget install --id Python.Python.3.12 --exact") == "approval-required"


def test_destructive_disk_command_is_forbidden():
    assert gate.classify_shell("format C:") == "forbidden"
    assert _decision("PowerShell", {"command": "format C:"}) == "deny"


def test_mcp_plan_is_allowed_without_execution(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_input": {"package_id": "Python.Python.3.12", "execute": False},
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_installed_plugin_scoped_mcp_execution_forces_host_prompt(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__package_install",
        "tool_input": {
            "package_id": "Python.Python.3.12",
            "execute": True,
            "user_approved": True,
        },
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_approval_required_capability_forces_host_prompt(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__capability_run",
        "tool_input": {"capability": "create-pr", "execute": True, "user_approved": True},
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_reversible_capability_is_not_auto_allowed(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__capability_run",
        "tool_input": {"capability": "lint-python", "execute": True},
    }
    assert gate.classify_tool_call(event["tool_name"], event["tool_input"]) == "reversible"
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_extra_args_upgrade_capability_authority(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__capability_run",
        "tool_input": {
            "capability": "lint-python",
            "extra_args": ["--fix"],
            "execute": True,
        },
    }
    assert gate.classify_tool_call(event["tool_name"], event["tool_input"]) == "approval-required"
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_even_read_only_capability_with_extra_args_is_reclassified():
    tool = "mcp__plugin_windows-dev-agent_windows-dev-agent__capability_run"
    assert gate.classify_tool_call(
        tool,
        {"capability": "inspect-git", "extra_args": ["--porcelain=v2"], "execute": True},
    ) == "approval-required"
