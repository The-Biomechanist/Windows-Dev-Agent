"""Windows Dev Agent MCP stdio server.

The server exposes a deliberately small orchestration surface.  Mutating tools
are plan-first and require ``execute=true``; approval-required execution also
requires ``user_approved=true``.  When used as the bundled Claude Code plugin,
PreToolUse hooks independently force the host permission dialog before those
calls reach this server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

from src.capabilities import (
    CapabilityConfigError,
    command_display,
    load_capabilities,
    run_capability,
    select_available_tool,
)

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = ROOT / "agent.log"


def _bool_property(description: str, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


TOOLS = [
    {
        "name": "env_inspect",
        "description": "Build a Windows development-environment snapshot using the native discovery layer.",
        "inputSchema": {
            "type": "object",
            "properties": {"force_refresh": _bool_property("Skip discovery cache and refresh machine state")},
        },
    },
    {
        "name": "tool_discover",
        "description": "Discover common runtimes, editors, package managers, and version-control tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["all", "runtimes", "editors", "package_managers", "vcs"],
                    "default": "all",
                }
            },
        },
    },
    {
        "name": "capability_run",
        "description": "Plan or execute a named capability from capabilities.yaml using the first available configured tool. Commands execute without a shell.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "Capability ID from capabilities.yaml"},
                "extra_args": {"type": "array", "items": {"type": "string"}, "default": []},
                "cwd": {"type": "string", "description": "Optional working directory"},
                "execute": _bool_property("Actually execute instead of returning the planned argv"),
                "user_approved": _bool_property("Set only after the user accepts the host permission prompt"),
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "workflow_plan",
        "description": "Build a deterministic execution scaffold from the registered capabilities for a task. The slash command can add richer task-specific reasoning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "context": {"type": "string", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "package_install",
        "description": "Plan or perform one package installation through WinGet, Chocolatey, or Scoop. Execution always goes through the host approval gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_id": {"type": "string"},
                "source": {"type": "string", "enum": ["winget", "chocolatey", "scoop"], "default": "winget"},
                "execute": _bool_property("Actually execute the install command"),
                "user_approved": _bool_property("Set only after the user accepts the host permission prompt"),
            },
            "required": ["package_id"],
        },
    },
    {
        "name": "sandbox_run",
        "description": "Plan or execute an isolated command using WSL, a dev container, or Windows Sandbox when available. Execution always goes through the host approval gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "environment": {"type": "string", "enum": ["auto", "wsl", "dev_container", "windows_sandbox"], "default": "auto"},
                "workspace_folder": {"type": "string", "default": "."},
                "execute": _bool_property("Actually launch the isolated command"),
                "user_approved": _bool_property("Set only after the user accepts the host permission prompt"),
            },
            "required": ["command"],
        },
    },
    {
        "name": "ecosystem_scan",
        "description": "Read-only inventory of VS Code extensions, MCP configuration, agent config files, Claude plugins, and optionally WinGet packages for /defrag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Project directory to inspect"},
                "include_packages": _bool_property("Also query winget list (slower)", False),
            },
        },
    },
    {
        "name": "logs_query",
        "description": "Query redacted structured session audit events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "last_n": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "filter": {"type": "string", "enum": ["all", "failures", "installs", "sandbox", "approvals"], "default": "all"},
            },
        },
    },
    {
        "name": "mcp_audit",
        "description": "Inspect MCP configuration files, report configured servers, duplicate names, and malformed entries without exposing environment secrets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "description": "Optional additional MCP JSON config file"}
            },
        },
    },
]


def _run(argv: list[str], *, cwd: Optional[Path] = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"succeeded": False, "error": str(exc), "argv": argv}
    return {
        "succeeded": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "argv": argv,
    }


def _resolve_cwd(value: Optional[str]) -> tuple[Optional[Path], Optional[str]]:
    if not value:
        return None, None
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        return None, f"Not a directory: {path}"
    return path, None


async def handle_env_inspect(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from src.discovery.discovery import EnvironmentDiscovery

        discovery = EnvironmentDiscovery(cache_enabled=True)
        snapshot = discovery.discover(force_refresh=bool(args.get("force_refresh", False)))
        return {"status": "ok", "snapshot": snapshot.to_dict()}
    except Exception as exc:
        return {
            "status": "degraded",
            "error": str(exc),
            "system": {"os_name": platform.system(), "os_version": platform.version()},
            "runtimes": {
                "python": {"available": True, "version": sys.version.split()[0]},
                "node": {"available": shutil.which("node") is not None},
                "cargo": {"available": shutil.which("cargo") is not None},
                "git": {"available": shutil.which("git") is not None},
            },
        }


async def handle_tool_discover(args: dict[str, Any]) -> dict[str, Any]:
    candidates = {
        "runtimes": ["python", "py", "node", "cargo", "go", "dotnet", "java"],
        "editors": ["code", "devenv", "rider", "pycharm"],
        "package_managers": ["winget", "choco", "scoop", "pip", "uv", "npm"],
        "vcs": ["git", "gh", "git-lfs"],
    }
    category = str(args.get("category", "all"))
    scan = candidates if category == "all" else {category: candidates.get(category, [])}
    output: dict[str, Any] = {}
    for group, commands in scan.items():
        output[group] = {}
        for command in commands:
            path = shutil.which(command)
            if not path:
                output[group][command] = {"available": False}
                continue
            probe = _run([command, "--version"], timeout=5)
            first_line = (probe.get("stdout") or probe.get("stderr") or "unknown").strip().splitlines()
            output[group][command] = {
                "available": True,
                "path": path,
                "version": first_line[0] if first_line else "unknown",
            }
    return output


async def handle_capability_run(args: dict[str, Any]) -> dict[str, Any]:
    return run_capability(
        str(args.get("capability", "")),
        execute=bool(args.get("execute", False)),
        user_approved=bool(args.get("user_approved", False)),
        extra_args=args.get("extra_args") or [],
        cwd=args.get("cwd"),
        timeout_seconds=int(args.get("timeout_seconds", 120)),
    )


def _task_tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_.+-]+", text) if len(token) > 1}


async def handle_workflow_plan(args: dict[str, Any]) -> dict[str, Any]:
    task = str(args.get("task", "")).strip()
    if not task:
        return {"status": "invalid_input", "error": "task is required"}
    try:
        capabilities = load_capabilities()
    except CapabilityConfigError as exc:
        return {"status": "configuration_error", "error": str(exc)}

    task_tokens = _task_tokens(task + " " + str(args.get("context", "")))
    ranked: list[tuple[int, str, Any]] = []
    for cap_id, capability in capabilities.items():
        haystack = _task_tokens(cap_id + " " + capability.description + " " + " ".join(capability.tags))
        score = len(task_tokens & haystack)
        ranked.append((score, cap_id, capability))
    ranked.sort(key=lambda row: (-row[0], row[1]))

    candidates = []
    for score, cap_id, capability in ranked[:3]:
        tool = select_available_tool(capability)
        candidates.append(
            {
                "capability": cap_id,
                "score": score,
                "description": capability.description,
                "safety_class": capability.safety,
                "available_tool": tool.name if tool else None,
            }
        )

    selected = candidates[0] if candidates and candidates[0]["score"] > 0 else None
    phases = [
        {
            "phase": "inspect",
            "entry": "Task and project boundary are known",
            "action": "env_inspect and project-local read-only inspection",
            "exit": "Relevant Windows/runtime/tool state is established",
            "safety_class": "read-only",
        },
        {
            "phase": "route",
            "entry": "Environment state is available",
            "action": f"Select capability {selected['capability']}" if selected else "Choose a capability or ordinary tool path from observed evidence",
            "exit": "One executable route and its safety class are explicit",
            "safety_class": "read-only",
        },
        {
            "phase": "execute",
            "entry": "Required host approval has been obtained when applicable",
            "action": "capability_run(execute=true) or the task's explicitly authorized tool",
            "exit": "The requested effect has an observed result",
            "safety_class": selected["safety_class"] if selected else "approval-required",
        },
        {
            "phase": "verify",
            "entry": "Execution returned",
            "action": "Run the narrowest relevant verification and inspect the changed surface",
            "exit": "Success or failure is established on the actual effect surface",
            "safety_class": "reversible",
        },
    ]
    return {
        "status": "planned",
        "task": task,
        "selected_candidate": selected,
        "candidate_capabilities": candidates,
        "phases": phases,
        "note": "This MCP tool is deterministic. /windows-dev-agent:plan can add task-specific tradeoff reasoning before execution.",
    }


_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


async def handle_package_install(args: dict[str, Any]) -> dict[str, Any]:
    package_id = str(args.get("package_id", ""))
    source = str(args.get("source", "winget"))
    if not _PACKAGE_ID.fullmatch(package_id):
        return {"status": "invalid_input", "error": "package_id contains unsupported characters"}

    commands = {
        "winget": ["winget", "install", "--id", package_id, "--exact", "--accept-package-agreements", "--accept-source-agreements"],
        "chocolatey": ["choco", "install", package_id, "-y"],
        "scoop": ["scoop", "install", package_id],
    }
    argv = commands[source]
    plan = {
        "status": "planned",
        "package_id": package_id,
        "source": source,
        "safety_class": "approval-required",
        "argv": argv,
        "command": command_display(argv),
        "requires_user_approval": True,
    }
    if not bool(args.get("execute", False)):
        return plan
    if not bool(args.get("user_approved", False)):
        return {**plan, "status": "approval_required", "error": "Host approval is required before installation"}
    if not shutil.which(argv[0]):
        return {**plan, "status": "unavailable", "error": f"{argv[0]} is not installed"}

    result = _run(argv, timeout=600)
    return {**plan, **result, "status": "completed" if result.get("succeeded") else "failed"}


def _windows_sandbox_executable() -> Optional[str]:
    found = shutil.which("WindowsSandbox.exe") or shutil.which("WindowsSandbox")
    if found:
        return found
    windir = os.environ.get("WINDIR")
    if windir:
        candidate = Path(windir) / "System32" / "WindowsSandbox.exe"
        if candidate.exists():
            return str(candidate)
    return None


def _choose_sandbox(environment: str) -> Optional[str]:
    if environment != "auto":
        return environment
    if shutil.which("wsl") or shutil.which("wsl.exe"):
        return "wsl"
    if shutil.which("devcontainer"):
        return "dev_container"
    if _windows_sandbox_executable():
        return "windows_sandbox"
    return None


def _prepare_windows_sandbox(command: str) -> tuple[Path, list[str]]:
    sandbox_exe = _windows_sandbox_executable()
    if not sandbox_exe:
        raise RuntimeError("Windows Sandbox is not available")

    temp_dir = Path(tempfile.mkdtemp(prefix="windows-dev-agent-sandbox-"))
    script_path = temp_dir / "run.cmd"
    script_path.write_text(
        "@echo off\r\n"
        "cd /d C:\\Users\\WDAGUtilityAccount\\Desktop\r\n"
        f"{command}\r\n"
        "set WDA_EXIT=%ERRORLEVEL%\r\n"
        "echo.\r\n"
        "echo Windows Dev Agent command exit code: %WDA_EXIT%\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    config_path = temp_dir / "run.wsb"
    host_folder = xml_escape(str(temp_dir))
    config_path.write_text(
        "<Configuration>\n"
        "  <Networking>Disable</Networking>\n"
        "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
        "  <MappedFolders>\n"
        "    <MappedFolder>\n"
        f"      <HostFolder>{host_folder}</HostFolder>\n"
        "      <SandboxFolder>C:\\WDAShare</SandboxFolder>\n"
        "      <ReadOnly>true</ReadOnly>\n"
        "    </MappedFolder>\n"
        "  </MappedFolders>\n"
        "  <LogonCommand><Command>cmd /d /s /c C:\\WDAShare\\run.cmd</Command></LogonCommand>\n"
        "</Configuration>\n",
        encoding="utf-8",
    )
    return config_path, [sandbox_exe, str(config_path)]


async def handle_sandbox_run(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        return {"status": "invalid_input", "error": "command is required"}

    environment = _choose_sandbox(str(args.get("environment", "auto")))
    if not environment:
        return {"status": "unavailable", "error": "No supported isolation environment is available"}

    workspace, cwd_error = _resolve_cwd(str(args.get("workspace_folder", ".")))
    if cwd_error:
        return {"status": "invalid_input", "error": cwd_error}

    if environment == "wsl":
        executable = shutil.which("wsl") or shutil.which("wsl.exe")
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "WSL is not available"}
        argv = [executable, "--", "bash", "-lc", command]
        launch_kind = "captured"
    elif environment == "dev_container":
        executable = shutil.which("devcontainer")
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "devcontainer CLI is not available"}
        argv = [executable, "exec", "--workspace-folder", str(workspace or Path.cwd()), "bash", "-lc", command]
        launch_kind = "captured"
    elif environment == "windows_sandbox":
        try:
            config_path, argv = _prepare_windows_sandbox(command)
        except RuntimeError as exc:
            return {"status": "unavailable", "environment": environment, "error": str(exc)}
        launch_kind = "interactive"
    else:
        return {"status": "invalid_input", "error": f"Unsupported environment: {environment}"}

    plan = {
        "status": "planned",
        "environment": environment,
        "safety_class": "approval-required",
        "argv": argv,
        "command": command_display(argv),
        "launch_kind": launch_kind,
        "requires_user_approval": True,
    }
    if environment == "windows_sandbox":
        plan["config_path"] = str(config_path)
        plan["cleanup_path"] = str(config_path.parent)

    if not bool(args.get("execute", False)):
        return plan
    if not bool(args.get("user_approved", False)):
        return {**plan, "status": "approval_required", "error": "Host approval is required before sandbox launch"}

    if launch_kind == "interactive":
        try:
            process = subprocess.Popen(argv, cwd=str(workspace) if workspace else None, shell=False)
        except OSError as exc:
            return {**plan, "status": "failed", "error": str(exc)}
        return {**plan, "status": "launched", "pid": process.pid}

    result = _run(argv, cwd=workspace, timeout=600)
    return {**plan, **result, "status": "completed" if result.get("succeeded") else "failed"}


def _safe_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, str(exc)
    return (value, None) if isinstance(value, dict) else (None, "root JSON value is not an object")


def _mcp_config_paths(cwd: Path, extra: Optional[str] = None) -> list[Path]:
    paths = [
        Path.home() / ".claude" / "claude_desktop_config.json",
        cwd / ".mcp.json",
        cwd / ".continue" / "config.json",
    ]
    if extra:
        paths.append(Path(extra).expanduser().resolve())
    seen: set[Path] = set()
    result = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _summarize_mcp_file(path: Path) -> dict[str, Any]:
    data, error = _safe_json(path)
    if error:
        return {"file": str(path), "error": error, "servers": []}
    servers = (data or {}).get("mcpServers", {})
    if not isinstance(servers, dict):
        return {"file": str(path), "error": "mcpServers is not an object", "servers": []}
    summary = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            summary.append({"name": str(name), "valid": False})
            continue
        summary.append(
            {
                "name": str(name),
                "valid": bool(spec.get("command") or spec.get("url")),
                "transport": "command" if spec.get("command") else "url" if spec.get("url") else "unknown",
                "command": spec.get("command"),
                "arg_count": len(spec.get("args", [])) if isinstance(spec.get("args", []), list) else None,
                "has_env": bool(spec.get("env")),
            }
        )
    return {"file": str(path), "servers": summary}


async def handle_ecosystem_scan(args: dict[str, Any]) -> dict[str, Any]:
    cwd, error = _resolve_cwd(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    cwd = cwd or Path.cwd().resolve()

    inventory: dict[str, Any] = {
        "project_root": str(cwd),
        "vscode": {"recommended": [], "installed": []},
        "mcp": [],
        "agent_configs": [],
        "claude_plugins": [],
        "packages": {"queried": False, "items": []},
        "warnings": [],
    }

    extensions_file = cwd / ".vscode" / "extensions.json"
    if extensions_file.exists():
        data, json_error = _safe_json(extensions_file)
        if json_error:
            inventory["warnings"].append(f"Could not parse {extensions_file}: {json_error}")
        elif isinstance((data or {}).get("recommendations"), list):
            inventory["vscode"]["recommended"] = (data or {})["recommendations"]

    code = shutil.which("code")
    if code:
        result = _run([code, "--list-extensions"], timeout=20)
        if result.get("succeeded"):
            inventory["vscode"]["installed"] = [line for line in result.get("stdout", "").splitlines() if line]
        else:
            inventory["warnings"].append("VS Code extension inventory failed")

    for path in _mcp_config_paths(cwd):
        if path.exists():
            inventory["mcp"].append(_summarize_mcp_file(path))

    config_candidates = [
        cwd / ".clinerules",
        cwd / ".roo",
        cwd / ".continue",
        cwd / ".github" / "copilot-instructions.md",
        cwd / "CLAUDE.md",
    ]
    inventory["agent_configs"] = [str(path) for path in config_candidates if path.exists()]

    plugin_dir = Path.home() / ".claude" / "plugins"
    if plugin_dir.is_dir():
        try:
            inventory["claude_plugins"] = sorted(path.name for path in plugin_dir.iterdir())
        except OSError as exc:
            inventory["warnings"].append(f"Could not inspect Claude plugins: {exc}")

    if bool(args.get("include_packages", False)):
        inventory["packages"]["queried"] = True
        winget = shutil.which("winget")
        if winget:
            result = _run([winget, "list", "--source", "winget"], timeout=90)
            if result.get("succeeded"):
                inventory["packages"]["items"] = result.get("stdout", "").splitlines()[:300]
            else:
                inventory["warnings"].append("winget list failed")
        else:
            inventory["warnings"].append("winget is not available")

    overlap_markers = ("cline", "roo", "continue", "copilot", "aider", "agent")
    installed = inventory["vscode"]["installed"]
    inventory["overlap_hints"] = [item for item in installed if any(marker in item.lower() for marker in overlap_markers)]
    return {"status": "ok", "inventory": inventory}


def _load_log_events() -> list[dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    events = []
    for line in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


async def handle_logs_query(args: dict[str, Any]) -> dict[str, Any]:
    events = _load_log_events()
    filter_name = str(args.get("filter", "all"))
    if filter_name == "failures":
        events = [event for event in events if event.get("success") is False]
    elif filter_name == "installs":
        events = [event for event in events if "package_install" in str(event.get("tool_name", ""))]
    elif filter_name == "sandbox":
        events = [event for event in events if "sandbox_run" in str(event.get("tool_name", ""))]
    elif filter_name == "approvals":
        events = [event for event in events if event.get("permission_decision") == "ask"]
    last_n = max(1, min(int(args.get("last_n", 20)), 200))
    return {"events": events[-last_n:], "matched": len(events)}


async def handle_mcp_audit(args: dict[str, Any]) -> dict[str, Any]:
    cwd = Path.cwd().resolve()
    configs = []
    for path in _mcp_config_paths(cwd, args.get("config_path")):
        if path.exists():
            configs.append(_summarize_mcp_file(path))

    names: list[str] = []
    malformed = []
    for config in configs:
        if config.get("error"):
            malformed.append({"file": config["file"], "error": config["error"]})
        for server in config.get("servers", []):
            names.append(server.get("name", ""))
            if not server.get("valid"):
                malformed.append({"file": config["file"], "server": server.get("name"), "error": "missing command/url"})
    counts = {name: names.count(name) for name in set(names)}
    duplicates = sorted(name for name, count in counts.items() if name and count > 1)
    return {
        "status": "ok" if not malformed else "issues_found",
        "configs": configs,
        "server_count": len(names),
        "duplicate_names": duplicates,
        "malformed": malformed,
    }


HANDLERS = {
    "env_inspect": handle_env_inspect,
    "tool_discover": handle_tool_discover,
    "capability_run": handle_capability_run,
    "workflow_plan": handle_workflow_plan,
    "package_install": handle_package_install,
    "sandbox_run": handle_sandbox_run,
    "ecosystem_scan": handle_ecosystem_scan,
    "logs_query": handle_logs_query,
    "mcp_audit": handle_mcp_audit,
}


async def handle_request(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = request.get("method", "")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "windows-dev-agent", "version": "0.2.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            result = await handler(tool_args)
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]},
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main_sync() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                response = loop.run_until_complete(handle_request(request))
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            if response is not None:
                sys.stdout.write(json.dumps(response, default=str) + "\n")
                sys.stdout.flush()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main_sync())
