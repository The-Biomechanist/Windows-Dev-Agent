"""Shared Windows Dev Agent MCP stdio runtime.

This module owns Windows behavior. Host adapters own package identity, project
binding, and human approval. Mutation-capable tools are plan-first, but the
runtime does not try to encode a host permission event in model-controlled tool
arguments. Forbidden capabilities remain blocked here as defense in depth.
"""

from __future__ import annotations

import asyncio
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
from typing import Any, Iterator, Optional
from xml.sax.saxutils import escape as xml_escape

from src import __version__
from src.capabilities import (
    CapabilityConfigError,
    command_display,
    load_capabilities,
    run_capability,
    select_available_tool,
)
from src.observability.trace import history_log_files

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / ".cache"
DATA_DIR = Path(os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
LOG_FILE = DATA_DIR / "agent.log"
ENVIRONMENT_CACHE_FILE = DATA_DIR / "environment.json"

MAX_SANDBOX_PAYLOAD_ENTRIES = 10_000
MAX_SANDBOX_PAYLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB staged input budget
MAX_JSON_CONFIG_BYTES = 2 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _default_project_dir() -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_PROJECT_DIR")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def _bool_property(description: str, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


TOOLS = [
    {
        "name": "env_inspect",
        "description": "Build a Windows environment snapshot whose availability fields preserve observed true/false/unknown state.",
        "inputSchema": {
            "type": "object",
            "properties": {"force_refresh": _bool_property("Skip the time-bound discovery cache")},
        },
    },
    {
        "name": "tool_discover",
        "description": "Discover common runtimes, editors, package managers, and version-control tools by resolving executables and running bounded version probes. External execution remains host-controlled.",
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
        "description": "Plan or execute a named argv-based capability in the active project. The active host owns approval for the exact executing call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string"},
                "extra_args": {"type": "array", "items": {"type": "string"}, "default": []},
                "cwd": {"type": "string", "description": "Working directory; defaults to the active host project"},
                "execute": _bool_property("Execute instead of returning the concrete plan"),
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "workflow_plan",
        "description": "Build a deterministic capability-aware execution scaffold for a task without collapsing tied or unavailable routes into a false selection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "context": {"type": "string", "default": ""},
                "cwd": {"type": "string", "description": "Project boundary for the plan"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "package_search",
        "description": "Execute one installed Windows package manager to search its configured source for candidate identities before installation. The requested effect is diagnostic, but external execution remains host-controlled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": "string", "enum": ["winget", "chocolatey", "scoop"], "default": "winget"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "package_install",
        "description": "Plan or execute one exact WinGet, Chocolatey, or Scoop install. Host approval surrounds the executing call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_id": {"type": "string"},
                "source": {"type": "string", "enum": ["winget", "chocolatey", "scoop"], "default": "winget"},
                "execute": _bool_property("Execute the reviewed install plan"),
            },
            "required": ["package_id"],
        },
    },
    {
        "name": "sandbox_run",
        "description": "Plan or run a command through WSL, a project Dev Container, or Windows Sandbox using an explicit isolation requirement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "environment": {"type": "string", "enum": ["auto", "wsl", "dev_container", "windows_sandbox"], "default": "auto"},
                "isolation_requirement": {
                    "type": "string",
                    "enum": ["linux_compatibility", "project_reproducibility", "untrusted_windows"],
                },
                "workspace_folder": {"type": "string", "description": "Active project/workspace"},
                "payload_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "Workspace-relative files/directories staged read-only into Windows Sandbox; required for auto untrusted_windows routing",
                },
                "execute": _bool_property("Launch the reviewed route"),
            },
            "required": ["command"],
        },
    },
    {
        "name": "ecosystem_scan",
        "description": "Inventory project agent/tool configuration; host-wide inventory is an explicit broader read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "include_host": _bool_property("Also inspect user-level agent/plugin/extension surfaces", False),
                "include_packages": _bool_property("With include_host, also query WinGet package inventory", False),
            },
        },
    },
    {
        "name": "logs_query",
        "description": "Query minimal persistent Windows Dev Agent audit metadata across all currently retained log segments.",
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
        "description": "Inspect project MCP configuration by default; host-level or arbitrary additional config reads are explicit broader reads.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "include_host": _bool_property("Also inspect supported user-level MCP config surfaces", False),
                "config_path": {"type": "string", "description": "Optional explicitly requested additional MCP JSON config"},
            },
        },
    },
]


def _run(argv: list[str], *, cwd: Optional[Path] = None, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "succeeded": False,
            "error": str(exc),
            "argv": argv,
            "execution_started": True,
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "succeeded": False,
            "error": str(exc),
            "argv": argv,
            "execution_started": False,
        }
    return {
        "succeeded": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "argv": argv,
        "execution_started": True,
    }


def _resolve_dir(value: Optional[str], *, default: Optional[Path] = None) -> tuple[Optional[Path], Optional[str]]:
    raw = value.strip() if isinstance(value, str) else ""
    path = Path(raw).expanduser().resolve() if raw else (default or _default_project_dir())
    if not path.is_dir():
        return None, f"Not a directory: {path}"
    return path, None


def _invalidate_environment_cache() -> bool:
    try:
        ENVIRONMENT_CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        return False
    return True


async def handle_env_inspect(args: dict[str, Any]) -> dict[str, Any]:
    try:
        from src.discovery.discovery import EnvironmentDiscovery

        snapshot = EnvironmentDiscovery(cache_enabled=True, data_dir=DATA_DIR).discover(
            force_refresh=bool(args.get("force_refresh", False))
        )
        return {
            "status": "ok" if snapshot.success else "degraded",
            "snapshot": snapshot.to_dict(),
        }
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
                output[group][command] = {"available": False, "version": None}
                continue
            probe = _run([command, "--version"], timeout=5)
            lines = (probe.get("stdout") or probe.get("stderr") or "").strip().splitlines()
            output[group][command] = {
                "available": True,
                "path": path,
                "version": lines[0] if probe.get("succeeded") and lines else None,
                "version_status": "known" if probe.get("succeeded") and lines else "unknown",
            }
    return output


async def handle_capability_run(args: dict[str, Any]) -> dict[str, Any]:
    cwd, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    return run_capability(
        str(args.get("capability", "")),
        execute=bool(args.get("execute", False)),
        extra_args=args.get("extra_args") or [],
        cwd=str(cwd),
        timeout_seconds=int(args.get("timeout_seconds", 120)),
    )


def _task_tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_.+-]+", text) if len(token) > 1}


async def handle_workflow_plan(args: dict[str, Any]) -> dict[str, Any]:
    task = str(args.get("task", "")).strip()
    if not task:
        return {"status": "invalid_input", "error": "task is required"}
    project, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    assert project is not None
    try:
        capabilities = load_capabilities()
    except CapabilityConfigError as exc:
        return {"status": "configuration_error", "error": str(exc)}

    task_tokens = _task_tokens(task + " " + str(args.get("context", "")))
    ranked: list[tuple[int, str, Any]] = []
    for cap_id, capability in capabilities.items():
        haystack = _task_tokens(cap_id + " " + capability.description + " " + " ".join(capability.tags))
        ranked.append((len(task_tokens & haystack), cap_id, capability))
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

    top_score = candidates[0]["score"] if candidates else 0
    top_candidates = [candidate for candidate in candidates if candidate["score"] == top_score] if top_score > 0 else []
    matched_candidate = top_candidates[0] if len(top_candidates) == 1 else None
    if top_score <= 0:
        selection_status = "no_match"
        selected = None
    elif len(top_candidates) > 1:
        selection_status = "ambiguous"
        selected = None
    elif matched_candidate and matched_candidate["available_tool"] is None:
        selection_status = "matched_unavailable"
        selected = None
    else:
        selection_status = "selected"
        selected = matched_candidate

    if selected:
        route_action = f"Select capability {selected['capability']}"
        execute_action = "Execute the selected capability or explicitly authorized tool path"
        execute_safety = selected["safety_class"]
    elif selection_status == "ambiguous":
        route_action = "Preserve the tied top capability candidates and obtain a discriminator before selecting one"
        execute_action = "Do not execute until the routing ambiguity is resolved"
        execute_safety = "unresolved"
    elif selection_status == "matched_unavailable" and matched_candidate:
        route_action = (
            f"Capability {matched_candidate['capability']} is the unique semantic match, but none of its configured tools are available; "
            "establish an executable tool or choose another supported route"
        )
        execute_action = "Do not execute until an available route is established"
        execute_safety = "unresolved"
    else:
        route_action = "Choose the smallest supported route from observed evidence"
        execute_action = "Do not execute until a supported route is established"
        execute_safety = "unresolved"

    phases = [
        {
            "phase": "inspect",
            "entry": "Task and project boundary are known",
            "action": "Obtain only environment/project facts that can change the route",
            "exit": "Relevant state is established or explicitly unknown",
            "safety_class": "read-only",
        },
        {
            "phase": "route",
            "entry": "Discriminating state is available",
            "action": route_action,
            "exit": "One executable route is selected or the exact routing blocker remains explicit",
            "safety_class": "read-only",
        },
        {
            "phase": "execute",
            "entry": "An executable route has been selected",
            "action": execute_action,
            "exit": "The action returns an observable result",
            "safety_class": execute_safety,
        },
        {
            "phase": "verify",
            "entry": "Execution returned",
            "action": "Observe the narrow surface where the requested effect should exist",
            "exit": "Success, failure, or unresolved state is established",
            "safety_class": "read-only",
        },
    ]
    return {
        "status": "planned",
        "task": task,
        "project_dir": str(project),
        "selection_status": selection_status,
        "matched_candidate": matched_candidate,
        "selected_candidate": selected,
        "candidate_capabilities": candidates,
        "phases": phases,
    }


_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


def _package_query(value: Any) -> Optional[str]:
    query = str(value or "").strip()
    if not query or len(query) > 160 or any(ord(ch) < 32 for ch in query):
        return None
    return query


async def handle_package_search(args: dict[str, Any]) -> dict[str, Any]:
    query = _package_query(args.get("query"))
    if query is None:
        return {"status": "invalid_input", "error": "query must be 1-160 printable characters"}
    source = str(args.get("source", "winget"))
    commands = {
        "winget": ["winget", "search", "--query", query, "--source", "winget", "--disable-interactivity"],
        "chocolatey": ["choco", "search", query, "--limit-output"],
        "scoop": ["scoop", "search", query],
    }
    argv = commands.get(source)
    if argv is None:
        return {"status": "invalid_input", "error": f"Unsupported package source: {source}"}
    if not shutil.which(argv[0]):
        return {"status": "unavailable", "source": source, "error": f"{argv[0]} is not installed"}
    result = _run(argv, timeout=90)
    return {
        "status": "completed" if result.get("succeeded") else "failed",
        "query": query,
        "source": source,
        **result,
    }


async def handle_package_install(args: dict[str, Any]) -> dict[str, Any]:
    package_id = str(args.get("package_id", ""))
    source = str(args.get("source", "winget"))
    if not _PACKAGE_ID.fullmatch(package_id):
        return {"status": "invalid_input", "error": "package_id contains unsupported characters"}
    commands = {
        "winget": ["winget", "install", "--id", package_id, "--exact", "--source", "winget", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
        "chocolatey": ["choco", "install", package_id, "-y"],
        "scoop": ["scoop", "install", package_id],
    }
    argv = commands.get(source)
    if argv is None:
        return {"status": "invalid_input", "error": f"Unsupported package source: {source}"}
    plan = {
        "status": "planned",
        "package_id": package_id,
        "source": source,
        "safety_class": "approval-required",
        "argv": argv,
        "command": command_display(argv),
        "requires_host_approval": True,
    }
    if not bool(args.get("execute", False)):
        return plan
    if not shutil.which(argv[0]):
        return {**plan, "status": "unavailable", "error": f"{argv[0]} is not installed", "execution_started": False}
    result = _run(argv, timeout=600)
    execution_started = result.get("execution_started") is True
    cache_invalidated = _invalidate_environment_cache() if execution_started else False
    return {
        **plan,
        **result,
        "status": "completed" if result.get("succeeded") else "failed",
        "environment_cache_invalidated": cache_invalidated,
    }


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


def _select_sandbox(environment: str, isolation_requirement: Optional[str], workspace: Path) -> tuple[Optional[str], Optional[str]]:
    if environment != "auto":
        return environment, None
    if isolation_requirement is None:
        return None, "isolation_requirement is required when environment=auto"
    if isolation_requirement == "linux_compatibility":
        return ("wsl", None) if (shutil.which("wsl") or shutil.which("wsl.exe")) else (None, "WSL is not available for linux_compatibility")
    if isolation_requirement == "project_reproducibility":
        has_config = (workspace / ".devcontainer").exists() or (workspace / ".devcontainer.json").exists()
        return ("dev_container", None) if shutil.which("devcontainer") and has_config else (None, "A configured Dev Container and devcontainer CLI are required for project_reproducibility")
    if isolation_requirement == "untrusted_windows":
        return ("windows_sandbox", None) if _windows_sandbox_executable() else (None, "Windows Sandbox is not available for untrusted_windows containment")
    return None, f"Unsupported isolation requirement: {isolation_requirement}"


def _is_reparse_point(path: Path) -> bool:
    """Return whether a Windows path is an NTFS reparse point without following it."""
    try:
        stat_result = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _walk_payload(root: Path) -> Iterator[Path]:
    """Walk a payload root without descending before the caller can reject a boundary."""
    stack = [root]
    while stack:
        current = stack.pop()
        yield current
        if current.is_dir():
            with os.scandir(current) as entries:
                children = [Path(entry.path) for entry in entries]
            stack.extend(reversed(children))


def _payload_sources(workspace: Path, value: Any) -> tuple[Optional[list[tuple[Path, Path]]], Optional[str]]:
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        return None, "payload_paths must be a list of non-empty workspace-relative paths"

    workspace = workspace.resolve()
    sources: list[tuple[Path, Path]] = []
    selected_parts: list[tuple[str, ...]] = []
    total_entries = 0
    total_bytes = 0

    for raw in value:
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            return None, f"payload path must stay inside the workspace: {raw}"
        rel_parts = tuple(part.casefold() for part in rel.parts)
        if any(
            rel_parts == existing
            or rel_parts[: len(existing)] == existing
            or existing[: len(rel_parts)] == rel_parts
            for existing in selected_parts
        ):
            return None, f"payload paths overlap or duplicate one another: {raw}"

        source = workspace / rel
        if not source.exists():
            return None, f"payload path does not exist: {raw}"
        if source.is_symlink() or _is_reparse_point(source):
            return None, f"payload path is a symbolic link or reparse point: {raw}"
        try:
            source.resolve().relative_to(workspace)
        except ValueError:
            return None, f"payload path escapes the workspace: {raw}"

        try:
            candidates = _walk_payload(source)
            for candidate in candidates:
                total_entries += 1
                if total_entries > MAX_SANDBOX_PAYLOAD_ENTRIES:
                    return None, "payload selection exceeds the Sandbox staging entry budget"
                if candidate.is_symlink() or _is_reparse_point(candidate):
                    return None, f"payload contains a symbolic link or reparse point: {candidate}"
                try:
                    candidate.resolve().relative_to(workspace)
                except ValueError:
                    return None, f"payload contains a path that escapes the workspace: {candidate}"
                if candidate.is_file():
                    total_bytes += candidate.stat().st_size
                    if total_bytes > MAX_SANDBOX_PAYLOAD_BYTES:
                        return None, "payload selection exceeds the Sandbox staging byte budget"
        except OSError as exc:
            return None, f"payload could not be enumerated safely: {exc}"

        selected_parts.append(rel_parts)
        sources.append((source, rel))
    return sources, None


def _prepare_windows_sandbox(command: str, payloads: list[tuple[Path, Path]]) -> tuple[Path, list[str]]:
    sandbox_exe = _windows_sandbox_executable()
    if not sandbox_exe:
        raise RuntimeError("Windows Sandbox is not available")
    temp_dir = Path(tempfile.mkdtemp(prefix="windows-dev-agent-sandbox-"))
    try:
        payload_dir = temp_dir / "payload"
        payload_dir.mkdir()
        for source, rel in payloads:
            destination = payload_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        script_path = temp_dir / "run.cmd"
        script_path.write_text(
            "@echo off\r\n"
            "cd /d C:\\WDAShare\\payload\r\n"
            f"{command}\r\n"
            "set WDA_EXIT=%ERRORLEVEL%\r\n"
            "echo.\r\n"
            "echo Windows Dev Agent command exit code: %WDA_EXIT%\r\n"
            "pause\r\n",
            encoding="utf-8",
        )
        config_path = temp_dir / "run.wsb"
        config_path.write_text(
            "<Configuration>\n"
            "  <Networking>Disable</Networking>\n"
            "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
            "  <MappedFolders><MappedFolder>\n"
            f"    <HostFolder>{xml_escape(str(temp_dir))}</HostFolder>\n"
            "    <SandboxFolder>C:\\WDAShare</SandboxFolder><ReadOnly>true</ReadOnly>\n"
            "  </MappedFolder></MappedFolders>\n"
            "  <LogonCommand><Command>cmd /d /s /c C:\\WDAShare\\run.cmd</Command></LogonCommand>\n"
            "</Configuration>\n",
            encoding="utf-8",
        )
        return config_path, [sandbox_exe, str(config_path)]
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


async def handle_sandbox_run(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        return {"status": "invalid_input", "error": "command is required"}
    workspace, cwd_error = _resolve_dir(args.get("workspace_folder"))
    if cwd_error:
        return {"status": "invalid_input", "error": cwd_error}
    assert workspace is not None
    requested_environment = str(args.get("environment", "auto"))
    requirement = args.get("isolation_requirement")
    isolation_requirement = str(requirement) if requirement is not None else None
    environment, route_error = _select_sandbox(requested_environment, isolation_requirement, workspace)
    if route_error:
        return {
            "status": "invalid_input" if requested_environment == "auto" and isolation_requirement is None else "unavailable",
            "environment": requested_environment,
            "isolation_requirement": isolation_requirement,
            "error": route_error,
        }
    assert environment is not None

    payloads, payload_error = _payload_sources(workspace, args.get("payload_paths", []))
    if payload_error:
        return {"status": "invalid_input", "error": payload_error}
    assert payloads is not None
    if requested_environment == "auto" and isolation_requirement == "untrusted_windows" and not payloads:
        return {
            "status": "invalid_input",
            "error": "payload_paths is required for auto untrusted_windows routing so the isolated workload exists inside Windows Sandbox",
        }

    if environment == "wsl":
        executable = shutil.which("wsl") or shutil.which("wsl.exe")
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "WSL is not available"}
        argv = [executable, "--cd", str(workspace), "--", "sh", "-lc", command]
        launch_kind = "captured"
    elif environment == "dev_container":
        executable = shutil.which("devcontainer")
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "devcontainer CLI is not available"}
        argv = [executable, "exec", "--workspace-folder", str(workspace), "sh", "-lc", command]
        launch_kind = "captured"
    elif environment == "windows_sandbox":
        executable = _windows_sandbox_executable()
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "Windows Sandbox is not available"}
        argv = [executable, "<generated-on-execute>.wsb"]
        launch_kind = "interactive"
    else:
        return {"status": "invalid_input", "error": f"Unsupported environment: {environment}"}

    plan = {
        "status": "planned",
        "environment": environment,
        "isolation_requirement": isolation_requirement,
        "project_dir": str(workspace),
        "payload_paths": [str(rel) for _, rel in payloads],
        "safety_class": "approval-required",
        "argv": argv,
        "command": command_display(argv),
        "launch_kind": launch_kind,
        "requires_host_approval": True,
        "config_materialized_on_execute": environment == "windows_sandbox",
    }
    if not bool(args.get("execute", False)):
        return plan

    if environment == "windows_sandbox":
        try:
            config_path, launch_argv = _prepare_windows_sandbox(command, payloads)
        except (OSError, RuntimeError) as exc:
            return {**plan, "status": "failed", "error": str(exc), "execution_started": False}
        try:
            process = subprocess.Popen(
                launch_argv,
                cwd=str(workspace),
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except OSError as exc:
            cleanup_path = config_path.parent
            shutil.rmtree(cleanup_path, ignore_errors=True)
            return {
                **plan,
                "status": "failed",
                "error": str(exc),
                "execution_started": False,
                "cleanup_performed": True,
            }
        return {
            **plan,
            "status": "launched",
            "argv": launch_argv,
            "command": command_display(launch_argv),
            "pid": process.pid,
            "config_path": str(config_path),
            "cleanup_path": str(config_path.parent),
            "execution_started": True,
        }

    result = _run(argv, cwd=workspace, timeout=600)
    return {
        **plan,
        **result,
        "status": "completed" if result.get("succeeded") else "failed",
    }


def _safe_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_CONFIG_BYTES + 1)
    except OSError as exc:
        return None, str(exc)
    if len(raw) > MAX_JSON_CONFIG_BYTES:
        return None, f"JSON config exceeds {MAX_JSON_CONFIG_BYTES} byte read limit"
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    return (value, None) if isinstance(value, dict) else (None, "root JSON value is not an object")


def _mcp_config_paths(project_dir: Path, *, include_host: bool = False, extra: Optional[str] = None) -> list[Path]:
    paths = [project_dir / ".mcp.json", project_dir / ".continue" / "config.json"]
    if include_host:
        paths.append(Path.home() / ".claude.json")
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    if extra:
        paths.append(Path(extra).expanduser().resolve())
    seen: set[Path] = set()
    result: list[Path] = []
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
        has_command = bool(spec.get("command"))
        has_url = bool(spec.get("url"))
        summary.append(
            {
                "name": str(name),
                "valid": has_command or has_url,
                "transport": "command" if has_command else "url" if has_url else "unknown",
                "has_command": has_command,
                "has_url": has_url,
                "arg_count": len(spec.get("args", [])) if isinstance(spec.get("args", []), list) else None,
                "has_env": bool(spec.get("env")),
            }
        )
    return {"file": str(path), "servers": summary}


async def handle_ecosystem_scan(args: dict[str, Any]) -> dict[str, Any]:
    project_dir, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    assert project_dir is not None
    include_host = bool(args.get("include_host", False))
    include_packages = bool(args.get("include_packages", False))
    if include_packages and not include_host:
        return {"status": "invalid_input", "error": "include_packages requires include_host=true"}

    inventory: dict[str, Any] = {
        "project_root": str(project_dir),
        "host_inventory_included": include_host,
        "vscode": {"recommended": [], "installed": []},
        "mcp": [],
        "agent_configs": [],
        "claude_plugins": [],
        "packages": {"queried": False, "items": []},
        "warnings": [],
    }
    extensions_file = project_dir / ".vscode" / "extensions.json"
    if extensions_file.exists():
        data, json_error = _safe_json(extensions_file)
        if json_error:
            inventory["warnings"].append(f"Could not parse {extensions_file}: {json_error}")
        elif isinstance((data or {}).get("recommendations"), list):
            inventory["vscode"]["recommended"] = (data or {})["recommendations"]

    for path in _mcp_config_paths(project_dir, include_host=include_host):
        if path.exists():
            inventory["mcp"].append(_summarize_mcp_file(path))

    config_candidates = [
        project_dir / ".clinerules",
        project_dir / ".roo",
        project_dir / ".continue",
        project_dir / ".agents",
        project_dir / ".github" / "copilot-instructions.md",
        project_dir / "CLAUDE.md",
    ]
    inventory["agent_configs"] = [str(path) for path in config_candidates if path.exists()]

    if include_host:
        code = shutil.which("code")
        if code:
            result = _run([code, "--list-extensions"], timeout=20)
            if result.get("succeeded"):
                inventory["vscode"]["installed"] = [line for line in result.get("stdout", "").splitlines() if line]
            else:
                inventory["warnings"].append("VS Code extension inventory failed")
        plugin_dir = Path.home() / ".claude" / "plugins"
        if plugin_dir.is_dir():
            try:
                inventory["claude_plugins"] = sorted(path.name for path in plugin_dir.iterdir())
            except OSError as exc:
                inventory["warnings"].append(f"Could not inspect Claude plugins: {exc}")
        if include_packages:
            inventory["packages"]["queried"] = True
            winget = shutil.which("winget")
            if winget:
                result = _run([winget, "list", "--source", "winget", "--disable-interactivity"], timeout=90)
                if result.get("succeeded"):
                    inventory["packages"]["items"] = result.get("stdout", "").splitlines()[:300]
                else:
                    inventory["warnings"].append("winget list failed")
            else:
                inventory["warnings"].append("winget is not available")

    overlap_markers = ("cline", "roo", "continue", "copilot", "aider", "agent")
    inventory["overlap_hints"] = [
        item for item in inventory["vscode"]["installed"]
        if any(marker in item.lower() for marker in overlap_markers)
    ]
    return {"status": "ok", "inventory": inventory}


def _load_log_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source in history_log_files(LOG_FILE):
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
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
        events = [
            event for event in events
            if event.get("execution_outcome") == "failed"
            or ("execution_outcome" not in event and event.get("success") is False)
        ]
    elif filter_name == "installs":
        events = [event for event in events if "package_install" in str(event.get("tool_name", ""))]
    elif filter_name == "sandbox":
        events = [event for event in events if "sandbox_run" in str(event.get("tool_name", ""))]
    elif filter_name == "approvals":
        events = [
            event for event in events
            if event.get("permission_decision") not in {None, "host-default"}
        ]
    last_n = max(1, min(int(args.get("last_n", 20)), 200))
    return {
        "events": events[-last_n:],
        "matched": len(events),
        "scope": "persistent_history",
        "data_dir": str(DATA_DIR),
    }


async def handle_mcp_audit(args: dict[str, Any]) -> dict[str, Any]:
    project_dir, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    assert project_dir is not None
    include_host = bool(args.get("include_host", False))
    configs = []
    for path in _mcp_config_paths(project_dir, include_host=include_host, extra=args.get("config_path")):
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
    return {
        "status": "ok" if not malformed else "issues_found",
        "project_dir": str(project_dir),
        "host_inventory_included": include_host,
        "configs": configs,
        "server_count": len(names),
        "duplicate_names": sorted(name for name, count in counts.items() if name and count > 1),
        "malformed": malformed,
    }


HANDLERS = {
    "env_inspect": handle_env_inspect,
    "tool_discover": handle_tool_discover,
    "capability_run": handle_capability_run,
    "workflow_plan": handle_workflow_plan,
    "package_search": handle_package_search,
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
                "serverInfo": {"name": "windows-dev-agent", "version": __version__},
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
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
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
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


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
