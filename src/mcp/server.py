"""Shared Windows Dev Agent MCP runtime.

This module owns Windows behavior. Host adapters own package identity, project
binding, stdio transport, and human approval. The shared core is intentionally
not a directly executable host runtime.
"""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
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
from src.discovery.discovery import EnvironmentDiscovery, invalidate_environment_cache
from src.execution import (
    executable_identity_matches,
    launch_bound,
    resolve_executable,
    resolve_windows_system_executable,
    run_bounded,
)
from src.file_guard import (
    FileBoundaryError,
    executable_identity,
    guarded_directory,
    guarded_open_read,
    valid_executable_identity,
)
from src.observability.trace import history_log_files
from src.windows_state import query_wsl_route_state

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / ".cache"
DATA_DIR = Path(os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
LOG_FILE = DATA_DIR / "agent.log"

MAX_SANDBOX_PAYLOAD_ENTRIES = 10_000
MAX_SANDBOX_PAYLOAD_BYTES = 1024 * 1024 * 1024
MAX_JSON_CONFIG_BYTES = 2 * 1024 * 1024
MAX_TOOL_ARGUMENTS = 32
MAX_TOOL_STRING_CHARS = 32_768
MAX_TOOL_ARRAY_ITEMS = 1_024
SANDBOX_STALE_SECONDS = 24 * 60 * 60
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_ROUTING_GENERIC_TOKENS = {
    "build", "check", "create", "current", "inspect", "lint", "package",
    "plan", "project", "route", "run", "setup", "test", "tool", "workflow",
}


def _default_project_dir() -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_PROJECT_DIR")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def _bool_property(description: str, default: bool = False) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": default}


def _expected_executable_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 4096,
        "description": "Absolute executable path returned by the reviewed execute=false plan; required when execute=true",
    }


def _expected_executable_identity_kind_property() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": ["file", "app_execution_alias"],
        "description": "Executable identity kind returned by the reviewed execute=false plan; required when execute=true",
    }


def _expected_executable_identity_sha256_property() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 64,
        "maxLength": 64,
        "description": "SHA-256 fingerprint of the reviewed executable identity material; required when execute=true",
    }


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
        "description": "Discover common runtimes, editors, package managers, and version-control tools by resolving exact executables and running bounded version probes. External execution remains host-controlled.",
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
        "description": "Plan or execute a named argv-based capability in the active project. Executing calls must bind the executable identity returned by the reviewed plan; the active host owns approval for the exact executing call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "maxLength": 256},
                "extra_args": {"type": "array", "items": {"type": "string"}, "maxItems": 128, "default": []},
                "cwd": {"type": "string", "description": "Working directory; defaults to the active host project"},
                "execute": _bool_property("Execute instead of returning the concrete plan"),
                "expected_executable": _expected_executable_property(),
                "expected_executable_identity_kind": _expected_executable_identity_kind_property(),
                "expected_executable_identity_sha256": _expected_executable_identity_sha256_property(),
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "workflow_plan",
        "description": "Build a deterministic capability-aware execution scaffold without treating weak, tied, or unavailable routing evidence as a selection.",
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
        "description": "Execute one installed Windows package manager to search its configured source for candidate identities before installation. External execution remains host-controlled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 160},
                "source": {"type": "string", "enum": ["winget", "chocolatey", "scoop"], "default": "winget"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "package_install",
        "description": "Plan or execute one exact WinGet, Chocolatey, or Scoop install. Executing calls must bind the executable identity returned by the reviewed plan; host approval surrounds the executing call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "maxLength": 256},
                "source": {"type": "string", "enum": ["winget", "chocolatey", "scoop"], "default": "winget"},
                "execute": _bool_property("Execute the reviewed install plan"),
                "expected_executable": _expected_executable_property(),
                "expected_executable_identity_kind": _expected_executable_identity_kind_property(),
                "expected_executable_identity_sha256": _expected_executable_identity_sha256_property(),
            },
            "required": ["package_id"],
        },
    },
    {
        "name": "sandbox_run",
        "description": "Plan or run a command through WSL, a project Dev Container, or Windows Sandbox under an explicit isolation requirement. Executing calls must bind the executable identity returned by the reviewed plan.",
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
                    "maxItems": 1_024,
                    "default": [],
                    "description": "Workspace-relative files/directories staged read-only into Windows Sandbox; required for every untrusted_windows request",
                },
                "execute": _bool_property("Launch the reviewed route"),
                "expected_executable": _expected_executable_property(),
                "expected_executable_identity_kind": _expected_executable_identity_kind_property(),
                "expected_executable_identity_sha256": _expected_executable_identity_sha256_property(),
            },
            "required": ["command", "isolation_requirement"],
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
for _tool in TOOLS:
    _tool["inputSchema"]["additionalProperties"] = False

_TOOL_BY_NAME = {str(tool["name"]): tool for tool in TOOLS}


def _validate_value(name: str, value: Any, schema: dict[str, Any]) -> Optional[str]:
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return f"{name} must be a string"
        limit = min(int(schema.get("maxLength", MAX_TOOL_STRING_CHARS)), MAX_TOOL_STRING_CHARS)
        if len(value) > limit:
            return f"{name} exceeds {limit} character limit"
    elif expected == "boolean":
        if type(value) is not bool:
            return f"{name} must be a boolean"
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{name} must be an integer"
        if "minimum" in schema and value < int(schema["minimum"]):
            return f"{name} must be >= {schema['minimum']}"
        if "maximum" in schema and value > int(schema["maximum"]):
            return f"{name} must be <= {schema['maximum']}"
    elif expected == "array":
        if not isinstance(value, list):
            return f"{name} must be an array"
        limit = min(int(schema.get("maxItems", MAX_TOOL_ARRAY_ITEMS)), MAX_TOOL_ARRAY_ITEMS)
        if len(value) > limit:
            return f"{name} exceeds {limit} item limit"
        item_schema = schema.get("items") or {}
        for index, item in enumerate(value):
            error = _validate_value(f"{name}[{index}]", item, item_schema)
            if error:
                return error
    if "enum" in schema and value not in schema["enum"]:
        return f"{name} must be one of {schema['enum']}"
    return None


def validate_tool_arguments(tool_name: str, arguments: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Strictly validate and default tool arguments at the execution boundary."""
    tool = _TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return None, f"Unknown tool: {tool_name}"
    if not isinstance(arguments, dict):
        return None, "tool arguments must be an object"
    if len(arguments) > MAX_TOOL_ARGUMENTS:
        return None, f"tool arguments exceed {MAX_TOOL_ARGUMENTS} field limit"
    schema = tool["inputSchema"]
    properties = schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        return None, f"unknown tool argument(s): {', '.join(unknown)}"
    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        return None, f"missing required tool argument(s): {', '.join(missing)}"

    normalized: dict[str, Any] = {}
    for name, prop_schema in properties.items():
        if name in arguments:
            value = arguments[name]
        elif "default" in prop_schema:
            value = deepcopy(prop_schema["default"])
        else:
            continue
        error = _validate_value(name, value, prop_schema)
        if error:
            return None, error
        normalized[name] = value
    return normalized, None


def _resolve_dir(value: Optional[str], *, default: Optional[Path] = None) -> tuple[Optional[Path], Optional[str]]:
    raw = value.strip() if isinstance(value, str) else ""
    path = Path(raw).expanduser().resolve() if raw else (default or _default_project_dir())
    if not path.is_dir():
        return None, f"Not a directory: {path}"
    return path, None


async def handle_env_inspect(args: dict[str, Any]) -> dict[str, Any]:
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=DATA_DIR)
    try:
        snapshot = discovery.discover(force_refresh=args.get("force_refresh") is True)
    except Exception as exc:
        logger.exception("Unexpected environment discovery failure")
        snapshot = discovery._fallback_discovery(f"Unexpected discovery failure: {exc}")
    return {
        "status": "ok" if snapshot.success else "degraded",
        "snapshot": snapshot.to_dict(),
        "execution_started": discovery.last_execution_started,
    }


async def handle_tool_discover(args: dict[str, Any]) -> dict[str, Any]:
    candidates = {
        "runtimes": ["python", "py", "node", "cargo", "go", "dotnet", "java"],
        "editors": ["code", "devenv", "rider", "pycharm"],
        "package_managers": ["winget", "choco", "scoop", "pip", "uv", "npm"],
        "vcs": ["git", "gh", "git-lfs"],
    }
    category = args.get("category", "all")
    scan = candidates if category == "all" else {category: candidates.get(str(category), [])}
    output: dict[str, Any] = {}
    execution_started = False
    for group, commands in scan.items():
        output[group] = {}
        for command in commands:
            path = resolve_executable(command)
            if not path:
                output[group][command] = {"available": False, "version": None}
                continue
            probe = run_bounded([path, "--version"], timeout=5)
            execution_started = execution_started or probe.get("execution_started") is True
            lines = (probe.get("stdout") or probe.get("stderr") or "").strip().splitlines()
            output[group][command] = {
                "available": True,
                "path": path,
                "version": lines[0] if probe.get("succeeded") and lines else None,
                "version_status": "known" if probe.get("succeeded") and lines else "unknown",
            }
    output["execution_started"] = execution_started
    return output


async def handle_capability_run(args: dict[str, Any]) -> dict[str, Any]:
    cwd, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    return run_capability(
        args.get("capability", ""),
        execute=args.get("execute") is True,
        extra_args=args.get("extra_args", []),
        cwd=str(cwd),
        timeout_seconds=args.get("timeout_seconds", 120),
        expected_executable=args.get("expected_executable"),
        expected_executable_identity_kind=args.get("expected_executable_identity_kind"),
        expected_executable_identity_sha256=args.get("expected_executable_identity_sha256"),
    )


def _task_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9_.+\-]+", text):
        token = raw.lower()
        pieces = [token, *re.split(r"[-_.+]+", token)]
        for piece in pieces:
            if len(piece) <= 1:
                continue
            tokens.add(piece)
            if len(piece) > 3 and piece.endswith("s") and not piece.endswith("ss"):
                tokens.add(piece[:-1])
    return tokens


async def handle_workflow_plan(args: dict[str, Any]) -> dict[str, Any]:
    task = args.get("task", "").strip()
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

    task_tokens = _task_tokens(task + " " + args.get("context", ""))
    candidates: list[dict[str, Any]] = []
    for cap_id, capability in capabilities.items():
        identity_tokens = _task_tokens(cap_id + " " + cap_id.replace("-", " ") + " " + " ".join(capability.tags))
        description_tokens = _task_tokens(capability.description)
        identity_matches = sorted(task_tokens & identity_tokens)
        discriminating_matches = sorted(set(identity_matches) - _ROUTING_GENERIC_TOKENS)
        description_matches = sorted(task_tokens & description_tokens)
        tool = select_available_tool(capability)
        candidates.append(
            {
                "capability": cap_id,
                "score": len(identity_matches),
                "identity_score": len(identity_matches),
                "discriminating_score": len(discriminating_matches),
                "description_score": len(description_matches),
                "identity_matches": identity_matches,
                "discriminating_matches": discriminating_matches,
                "description_matches": description_matches,
                "description": capability.description,
                "safety_class": capability.safety,
                "available_tool": tool.name if tool else None,
                "configured_tools": [configured.name for configured in capability.tools],
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["discriminating_score"]),
            -int(row["identity_score"]),
            -int(row["description_score"]),
            str(row["capability"]),
        )
    )

    matched_candidate: Optional[dict[str, Any]] = None
    selected: Optional[dict[str, Any]] = None
    selection_status = "no_match"
    route_discriminator: Optional[dict[str, Any]] = None
    eligible = [candidate for candidate in candidates if int(candidate["discriminating_score"]) > 0]
    if eligible:
        top_rank = (
            int(eligible[0]["discriminating_score"]),
            int(eligible[0]["identity_score"]),
            int(eligible[0]["description_score"]),
        )
        top = [
            candidate for candidate in eligible
            if (
                int(candidate["discriminating_score"]),
                int(candidate["identity_score"]),
                int(candidate["description_score"]),
            ) == top_rank
        ]
        if len(top) > 1:
            selection_status = "ambiguous"
            route_discriminator = {
                "rank": list(top_rank),
                "candidates": [candidate["capability"] for candidate in top],
                "availability": {str(candidate["capability"]): candidate.get("available_tool") for candidate in top},
                "reason": "Multiple capabilities have equal strongest task evidence. Tool availability is an execution prerequisite, not evidence that resolves the semantic route.",
            }
        else:
            matched_candidate = top[0]
            if matched_candidate.get("available_tool"):
                selection_status = "selected"
                selected = matched_candidate
            else:
                selection_status = "matched_unavailable"
                route_discriminator = {
                    "rank": list(top_rank),
                    "candidates": [matched_candidate["capability"]],
                    "configured_tools": {str(matched_candidate["capability"]): list(matched_candidate["configured_tools"])},
                    "reason": "The uniquely strongest semantic route has no configured executable currently available; do not silently fall through to a weaker match.",
                }
    else:
        weak = [candidate for candidate in candidates if int(candidate["identity_score"]) > 0 or int(candidate["description_score"]) > 0]
        if weak:
            route_discriminator = {
                "candidates": [candidate["capability"] for candidate in weak[:5]],
                "reason": "Only generic identity overlap or description-only similarity was observed. That evidence is insufficient for deterministic capability selection.",
            }

    if selected:
        route_action = f"Select capability {selected['capability']}"
        execute_action = "Execute the selected capability or explicitly authorized tool path"
        execute_safety = selected["safety_class"]
    elif selection_status == "ambiguous":
        route_action = "Preserve the tied top capability candidates and obtain a semantic discriminator before selecting one"
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
        route_action = "Choose a capability or ordinary tool path only after task evidence distinguishes a supported route"
        execute_action = "Do not execute until a supported route is established"
        execute_safety = "unresolved"

    phases = [
        {"phase": "inspect", "entry": "Task and project boundary are known", "action": "Obtain only environment/project facts that can change the route", "exit": "Relevant state is established or explicitly unknown", "safety_class": "read-only"},
        {"phase": "route", "entry": "Discriminating state is available", "action": route_action, "exit": "One executable route is selected or the exact routing blocker remains explicit", "safety_class": "read-only"},
        {"phase": "execute", "entry": "An executable route has been selected", "action": execute_action, "exit": "The action returns an observable result", "safety_class": execute_safety},
        {"phase": "verify", "entry": "Execution returned", "action": "Observe the narrow surface where the requested effect should exist", "exit": "Success, failure, or unresolved state is established", "safety_class": "read-only"},
    ]
    return {
        "status": "planned",
        "task": task,
        "project_dir": str(project),
        "selection_status": selection_status,
        "matched_candidate": matched_candidate,
        "selected_candidate": selected,
        "route_discriminator": route_discriminator,
        "candidate_capabilities": candidates,
        "phases": phases,
    }


_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


def _package_query(value: Any) -> Optional[str]:
    query = value.strip() if isinstance(value, str) else ""
    if not query or len(query) > 160 or any(ord(ch) < 32 for ch in query):
        return None
    return query


def _resolve_argv(argv: list[str]) -> Optional[list[str]]:
    executable = resolve_executable(argv[0]) if argv else None
    return [executable, *argv[1:]] if executable else None


async def handle_package_search(args: dict[str, Any]) -> dict[str, Any]:
    query = _package_query(args.get("query"))
    if query is None:
        return {"status": "invalid_input", "error": "query must be 1-160 printable characters"}
    source = args.get("source", "winget")
    commands = {
        "winget": ["winget", "search", "--query", query, "--source", "winget", "--disable-interactivity"],
        "chocolatey": ["choco", "search", query, "--limit-output"],
        "scoop": ["scoop", "search", query],
    }
    configured = commands.get(source)
    if configured is None:
        return {"status": "invalid_input", "error": f"Unsupported package source: {source}"}
    argv = _resolve_argv(configured)
    if argv is None:
        return {"status": "unavailable", "source": source, "error": f"{configured[0]} is not installed"}
    result = run_bounded(argv, timeout=90)
    return {"status": "completed" if result.get("succeeded") else "failed", "query": query, "source": source, **result}


async def handle_package_install(args: dict[str, Any]) -> dict[str, Any]:
    package_id = args.get("package_id", "")
    source = args.get("source", "winget")
    if not isinstance(package_id, str) or not _PACKAGE_ID.fullmatch(package_id):
        return {"status": "invalid_input", "error": "package_id contains unsupported characters"}
    commands = {
        "winget": ["winget", "install", "--id", package_id, "--exact", "--source", "winget", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
        "chocolatey": ["choco", "install", package_id, "-y"],
        "scoop": ["scoop", "install", package_id],
    }
    configured = commands.get(source)
    if configured is None:
        return {"status": "invalid_input", "error": f"Unsupported package source: {source}"}
    resolved = _resolve_argv(configured)
    argv = resolved or configured
    identity = executable_identity(resolved[0]) if resolved else None
    plan = {
        "status": "planned",
        "package_id": package_id,
        "source": source,
        "safety_class": "approval-required",
        "executable": resolved[0] if resolved else None,
        "executable_identity_kind": identity.kind if identity else None,
        "executable_identity_sha256": identity.sha256 if identity else None,
        "argv": argv,
        "command": command_display(argv),
        "requires_host_approval": True,
        "executable_resolved": resolved is not None,
    }
    if resolved is not None and identity is None:
        return {
            **plan,
            "status": "unavailable",
            "error": "Package-manager executable identity could not be established",
            "execution_started": False,
        }
    if args.get("execute") is not True:
        return plan
    if resolved is None:
        return {**plan, "status": "unavailable", "error": f"{configured[0]} is not installed", "execution_started": False}
    expected_executable = args.get("expected_executable")
    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, resolved[0]):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }
    expected_kind = args.get("expected_executable_identity_kind")
    expected_sha256 = args.get("expected_executable_identity_sha256")
    if not valid_executable_identity(expected_kind, expected_sha256):
        return {
            **plan,
            "status": "invalid_input",
            "error": "reviewed executable identity kind and fingerprint are required for execution",
            "execution_started": False,
        }
    if identity is None or identity.kind != expected_kind or identity.sha256 != expected_sha256.lower():
        return {
            **plan,
            "status": "stale_plan",
            "error": "Executable identity no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    cache_invalidated = invalidate_environment_cache(DATA_DIR)
    if not cache_invalidated:
        return {
            **plan,
            "status": "failed",
            "error": "Environment cache invalidation could not be established; installer was not started",
            "execution_started": False,
            "environment_cache_invalidated": False,
        }
    result = run_bounded(
        resolved,
        timeout=600,
        expected_executable_identity_kind=expected_kind,
        expected_executable_identity_sha256=expected_sha256.lower(),
    )
    if result.get("identity_mismatch") is True:
        return {
            **plan,
            **result,
            "status": "stale_plan",
            "error": "Executable identity changed after plan validation; obtain a fresh plan before execution",
            "environment_cache_invalidated": True,
        }
    return {
        **plan,
        **result,
        "status": "completed" if result.get("succeeded") else "failed",
        "environment_cache_invalidated": True,
    }


def _windows_sandbox_executable() -> Optional[str]:
    return resolve_windows_system_executable("WindowsSandbox.exe")


def _wsl_executable() -> Optional[str]:
    return resolve_windows_system_executable("wsl.exe")


_REQUIRED_SANDBOX_BACKEND = {
    "linux_compatibility": "wsl",
    "project_reproducibility": "dev_container",
    "untrusted_windows": "windows_sandbox",
}


def _select_sandbox(environment: str, isolation_requirement: Optional[str], workspace: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if isolation_requirement is None:
        return None, "isolation_requirement is required for every sandbox_run request", "invalid_input"
    expected = _REQUIRED_SANDBOX_BACKEND.get(isolation_requirement)
    if expected is None:
        return None, f"Unsupported isolation requirement: {isolation_requirement}", "invalid_input"
    if environment != "auto" and environment != expected:
        return None, f"environment={environment} does not satisfy isolation_requirement={isolation_requirement}; required backend is {expected}", "invalid_input"
    selected = expected
    if selected == "wsl":
        wsl_executable = _wsl_executable()
        wsl_state = query_wsl_route_state(wsl_executable)
        if wsl_state.available is not True:
            status = "unavailable" if wsl_state.available is False else "unknown"
            return None, wsl_state.reason or "WSL route state could not be established", status
    if selected == "dev_container":
        has_config = False
        for config_path in (workspace / ".devcontainer" / "devcontainer.json", workspace / ".devcontainer.json"):
            exists, containment_error = _project_path_status(workspace, config_path)
            if containment_error:
                return None, containment_error, "invalid_input"
            has_config = has_config or exists
        if not resolve_executable("devcontainer") or not has_config:
            return None, "A configured Dev Container and devcontainer CLI are required for project_reproducibility", "unavailable"
    if selected == "windows_sandbox" and not _windows_sandbox_executable():
        return None, "Windows Sandbox is not available for untrusted_windows containment", "unavailable"
    return selected, None, None


def _is_reparse_point(path: Path) -> bool:
    stat_result = os.stat(path, follow_symlinks=False)
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _walk_payload(root: Path) -> Iterator[Path]:
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
        if any(rel_parts == existing or rel_parts[: len(existing)] == existing or existing[: len(rel_parts)] == rel_parts for existing in selected_parts):
            return None, f"payload paths overlap or duplicate one another: {raw}"

        source = workspace / rel
        current = workspace
        for part in rel.parts:
            current = current / part
            try:
                if current.is_symlink() or _is_reparse_point(current):
                    return None, f"payload path crosses a symbolic link or reparse point: {current}"
            except FileNotFoundError:
                return None, f"payload path does not exist: {raw}"
            except OSError as exc:
                return None, f"payload metadata could not be established for {current}: {exc}"

        if not source.exists():
            return None, f"payload path does not exist: {raw}"
        try:
            source.resolve().relative_to(workspace)
        except ValueError:
            return None, f"payload path escapes the workspace: {raw}"

        try:
            for candidate in _walk_payload(source):
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


def _sandbox_state_root() -> Path:
    return DATA_DIR / "sandbox-runs"


def _cleanup_stale_sandbox_bundles() -> None:
    root = _sandbox_state_root()
    try:
        if not root.is_dir():
            return
        now = time.time()
        for candidate in root.iterdir():
            if not candidate.name.startswith("run-"):
                continue
            try:
                if candidate.is_symlink() or _is_reparse_point(candidate) or not candidate.is_dir():
                    continue
                marker = candidate / ".windows-dev-agent-bundle"
                if not marker.is_file() or now - candidate.stat().st_mtime < SANDBOX_STALE_SECONDS:
                    continue
                shutil.rmtree(candidate)
            except OSError:
                continue
    except OSError:
        return


def _stage_windows_sandbox_payloads(workspace: Path, payloads: list[tuple[Path, Path]], payload_dir: Path) -> None:
    """Copy only bytes read from use-time verified workspace handles into WDA staging."""
    workspace = workspace.resolve()
    budget = {"entries": 0, "bytes": 0}

    def stage(source: Path, destination: Path) -> None:
        budget["entries"] += 1
        if budget["entries"] > MAX_SANDBOX_PAYLOAD_ENTRIES:
            raise RuntimeError("payload selection exceeds the Sandbox staging entry budget")
        try:
            if source.is_symlink() or _is_reparse_point(source):
                raise RuntimeError(f"payload path crossed a symbolic link or reparse point during staging: {source}")
            if source.is_dir():
                with guarded_directory(source, root=workspace, exact_path=True):
                    destination.mkdir(parents=True, exist_ok=True)
                    with os.scandir(source) as entries:
                        children = [Path(entry.path) for entry in entries]
                    for child in children:
                        stage(child, destination / child.name)
                return
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with guarded_open_read(source, root=workspace, exact_path=True) as incoming:
                    with destination.open("xb") as outgoing:
                        while True:
                            chunk = incoming.read(1024 * 1024)
                            if not chunk:
                                break
                            budget["bytes"] += len(chunk)
                            if budget["bytes"] > MAX_SANDBOX_PAYLOAD_BYTES:
                                raise RuntimeError("payload selection exceeds the Sandbox staging byte budget")
                            outgoing.write(chunk)
                return
        except FileBoundaryError as exc:
            raise RuntimeError(str(exc)) from exc
        except OSError as exc:
            raise RuntimeError(f"payload could not be staged safely: {exc}") from exc
        raise RuntimeError(f"payload path is not a regular file or directory: {source}")

    for _source, rel in payloads:
        stage(workspace / rel, payload_dir / rel)


def _prepare_windows_sandbox(command: str, workspace: Path, payloads: list[tuple[Path, Path]], sandbox_executable: str) -> tuple[Path, Path, list[str]]:
    if not isinstance(sandbox_executable, str) or not sandbox_executable:
        raise RuntimeError("Windows Sandbox executable identity was not established")
    root = _sandbox_state_root()
    root.mkdir(parents=True, exist_ok=True)
    bundle_root = Path(tempfile.mkdtemp(prefix="run-", dir=str(root)))
    try:
        (bundle_root / ".windows-dev-agent-bundle").write_text("1", encoding="ascii")
        share_dir = bundle_root / "share"
        payload_dir = share_dir / "payload"
        payload_dir.mkdir(parents=True)
        _stage_windows_sandbox_payloads(workspace, payloads, payload_dir)
        script_path = share_dir / "run.cmd"
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
        config_path = bundle_root / "run.wsb"
        config_path.write_text(
            "<Configuration>\n"
            "  <vGPU>Disable</vGPU>\n"
            "  <Networking>Disable</Networking>\n"
            "  <AudioInput>Disable</AudioInput>\n"
            "  <VideoInput>Disable</VideoInput>\n"
            "  <PrinterRedirection>Disable</PrinterRedirection>\n"
            "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
            "  <MappedFolders><MappedFolder>\n"
            f"    <HostFolder>{xml_escape(str(share_dir))}</HostFolder>\n"
            "    <SandboxFolder>C:\\WDAShare</SandboxFolder><ReadOnly>true</ReadOnly>\n"
            "  </MappedFolder></MappedFolders>\n"
            "  <LogonCommand><Command>cmd /d /s /c C:\\WDAShare\\run.cmd</Command></LogonCommand>\n"
            "</Configuration>\n",
            encoding="utf-8",
        )
        return bundle_root, config_path, [sandbox_executable, str(config_path)]
    except Exception:
        shutil.rmtree(bundle_root, ignore_errors=True)
        raise


def _cleanup_after_sandbox_exit(process: subprocess.Popen[Any], bundle_root: Path) -> None:
    try:
        process.wait()
    except Exception:
        return
    shutil.rmtree(bundle_root, ignore_errors=True)


async def handle_sandbox_run(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command", "").strip()
    if not command:
        return {"status": "invalid_input", "error": "command is required"}
    workspace, cwd_error = _resolve_dir(args.get("workspace_folder"))
    if cwd_error:
        return {"status": "invalid_input", "error": cwd_error}
    assert workspace is not None
    requested_environment = args.get("environment", "auto")
    isolation_requirement = args.get("isolation_requirement")
    environment, route_error, route_status = _select_sandbox(requested_environment, isolation_requirement, workspace)
    if route_error:
        return {
            "status": route_status or "invalid_input",
            "environment": requested_environment,
            "isolation_requirement": isolation_requirement,
            "error": route_error,
        }
    assert environment is not None

    payloads, payload_error = _payload_sources(workspace, args.get("payload_paths", []))
    if payload_error:
        return {"status": "invalid_input", "error": payload_error}
    assert payloads is not None
    if isolation_requirement == "untrusted_windows" and not payloads:
        return {"status": "invalid_input", "error": "payload_paths is required for every untrusted_windows request"}
    if isolation_requirement != "untrusted_windows" and payloads:
        return {"status": "invalid_input", "error": "payload_paths is only valid for untrusted_windows Windows Sandbox requests"}

    if environment == "wsl":
        executable = _wsl_executable()
        if not executable:
            return {"status": "unavailable", "environment": environment, "error": "WSL is not available"}
        argv = [executable, "--cd", str(workspace), "--", "sh", "-lc", command]
        launch_kind = "captured"
    elif environment == "dev_container":
        executable = resolve_executable("devcontainer")
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

    identity = executable_identity(executable)
    if identity is None:
        return {
            "status": "unavailable",
            "environment": environment,
            "error": "Sandbox backend executable identity could not be established",
            "execution_started": False,
        }

    plan = {
        "status": "planned",
        "environment": environment,
        "isolation_requirement": isolation_requirement,
        "project_dir": str(workspace),
        "payload_paths": [str(rel) for _, rel in payloads],
        "safety_class": "approval-required",
        "executable": executable,
        "executable_identity_kind": identity.kind,
        "executable_identity_sha256": identity.sha256,
        "argv": argv,
        "command": command_display(argv),
        "launch_kind": launch_kind,
        "requires_host_approval": True,
        "config_materialized_on_execute": environment == "windows_sandbox",
    }
    if args.get("execute") is not True:
        return plan

    expected_executable = args.get("expected_executable")
    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, executable):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }
    expected_kind = args.get("expected_executable_identity_kind")
    expected_sha256 = args.get("expected_executable_identity_sha256")
    if not valid_executable_identity(expected_kind, expected_sha256):
        return {
            **plan,
            "status": "invalid_input",
            "error": "reviewed executable identity kind and fingerprint are required for execution",
            "execution_started": False,
        }
    if identity.kind != expected_kind or identity.sha256 != expected_sha256.lower():
        return {
            **plan,
            "status": "stale_plan",
            "error": "Executable identity no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    if environment == "windows_sandbox":
        _cleanup_stale_sandbox_bundles()
        try:
            bundle_root, _config_path, launch_argv = _prepare_windows_sandbox(command, workspace, payloads, executable)
        except (OSError, RuntimeError) as exc:
            return {**plan, "status": "failed", "error": str(exc), "execution_started": False}
        launch = launch_bound(
            launch_argv,
            cwd=workspace,
            expected_executable_identity_kind=expected_kind,
            expected_executable_identity_sha256=expected_sha256.lower(),
            stdin=subprocess.DEVNULL,
        )
        process = launch.get("process")
        if process is None:
            shutil.rmtree(bundle_root, ignore_errors=True)
            status = "stale_plan" if launch.get("identity_mismatch") is True else "failed"
            error = (
                "Executable identity changed after plan validation; obtain a fresh plan before execution"
                if status == "stale_plan" else str(launch.get("error", "Sandbox launch failed"))
            )
            return {**plan, **launch, "status": status, "error": error, "cleanup_performed": True}
        threading.Thread(target=_cleanup_after_sandbox_exit, args=(process, bundle_root), daemon=True).start()
        return {
            **plan,
            "status": "launched",
            "argv": [launch_argv[0], "<managed-config>.wsb"],
            "command": command_display([launch_argv[0], "<managed-config>.wsb"]),
            "pid": process.pid,
            "cleanup_managed": True,
            "execution_started": True,
        }

    result = run_bounded(
        argv,
        cwd=workspace,
        timeout=600,
        expected_executable_identity_kind=expected_kind,
        expected_executable_identity_sha256=expected_sha256.lower(),
    )
    if result.get("identity_mismatch") is True:
        return {
            **plan,
            **result,
            "status": "stale_plan",
            "error": "Executable identity changed after plan validation; obtain a fresh plan before execution",
        }
    return {**plan, **result, "status": "completed" if result.get("succeeded") else "failed"}


def _project_path_status(project_root: Path, path: Path) -> tuple[bool, Optional[str]]:
    """Establish that a project-local path reaches no symlink/reparse boundary."""
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return False, "path is outside the project boundary"
    current = project_root
    for part in relative.parts:
        current = current / part
        try:
            os.stat(current, follow_symlinks=False)
        except FileNotFoundError:
            return False, None
        except OSError as exc:
            return False, f"could not establish project path metadata for {current}: {exc}"
        try:
            if current.is_symlink() or _is_reparse_point(current):
                return False, f"project path crosses a symbolic link or reparse point: {current}"
        except OSError as exc:
            return False, f"could not establish project path metadata for {current}: {exc}"
    try:
        path.resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return False, "project path resolves outside the project boundary"
    return True, None


def _safe_json(path: Path, *, project_root: Optional[Path] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if project_root is not None:
        exists, containment_error = _project_path_status(project_root, path)
        if containment_error:
            return None, containment_error
        if not exists:
            return None, "missing"
    try:
        if project_root is not None:
            with guarded_open_read(path, root=project_root, exact_path=True) as handle:
                raw = handle.read(MAX_JSON_CONFIG_BYTES + 1)
        else:
            with path.open("rb") as handle:
                raw = handle.read(MAX_JSON_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None, "missing"
    except FileBoundaryError as exc:
        return None, str(exc)
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


def _is_lexically_project_local(path: Path, project_dir: Path) -> bool:
    try:
        path.relative_to(project_dir)
        return True
    except ValueError:
        return False


def _summarize_mcp_file(path: Path, *, project_root: Optional[Path] = None) -> Optional[dict[str, Any]]:
    data, error = _safe_json(path, project_root=project_root)
    if error == "missing":
        return None
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
        summary.append({
            "name": str(name),
            "valid": has_command or has_url,
            "transport": "command" if has_command else "url" if has_url else "unknown",
            "has_command": has_command,
            "has_url": has_url,
            "arg_count": len(spec.get("args", [])) if isinstance(spec.get("args", []), list) else None,
            "has_env": bool(spec.get("env")),
        })
    return {"file": str(path), "servers": summary}


async def handle_ecosystem_scan(args: dict[str, Any]) -> dict[str, Any]:
    project_dir, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    assert project_dir is not None
    include_host = args.get("include_host") is True
    include_packages = args.get("include_packages") is True
    if include_packages and not include_host:
        return {"status": "invalid_input", "error": "include_packages requires include_host=true"}

    execution_started = False
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
    data, json_error = _safe_json(extensions_file, project_root=project_dir)
    if json_error not in {None, "missing"}:
        inventory["warnings"].append(f"Could not parse {extensions_file}: {json_error}")
    elif isinstance((data or {}).get("recommendations"), list):
        inventory["vscode"]["recommended"] = (data or {})["recommendations"]

    for path in _mcp_config_paths(project_dir, include_host=include_host):
        project_root = project_dir if _is_lexically_project_local(path, project_dir) else None
        summary = _summarize_mcp_file(path, project_root=project_root)
        if summary is not None:
            inventory["mcp"].append(summary)

    config_candidates = [
        project_dir / ".clinerules",
        project_dir / ".roo",
        project_dir / ".continue",
        project_dir / ".agents",
        project_dir / ".github" / "copilot-instructions.md",
        project_dir / "CLAUDE.md",
    ]
    for path in config_candidates:
        exists, path_error = _project_path_status(project_dir, path)
        if path_error:
            inventory["warnings"].append(path_error)
        elif exists:
            inventory["agent_configs"].append(str(path))

    if include_host:
        code = resolve_executable("code")
        if code:
            result = run_bounded([code, "--list-extensions"], timeout=20)
            execution_started = execution_started or result.get("execution_started") is True
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
            winget = resolve_executable("winget")
            if winget:
                result = run_bounded([winget, "list", "--source", "winget", "--disable-interactivity"], timeout=90, stdout_bytes=64 * 1024)
                execution_started = execution_started or result.get("execution_started") is True
                if result.get("succeeded"):
                    inventory["packages"]["items"] = result.get("stdout", "").splitlines()[:300]
                else:
                    inventory["warnings"].append("winget list failed")
            else:
                inventory["warnings"].append("winget is not available")

    overlap_markers = ("cline", "roo", "continue", "copilot", "aider", "agent")
    inventory["overlap_hints"] = [item for item in inventory["vscode"]["installed"] if any(marker in item.lower() for marker in overlap_markers)]
    return {"status": "ok", "inventory": inventory, "execution_started": execution_started}


def _load_log_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source in history_log_files(LOG_FILE):
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    return events


async def handle_logs_query(args: dict[str, Any]) -> dict[str, Any]:
    events = _load_log_events()
    filter_name = args.get("filter", "all")
    if filter_name == "failures":
        events = [event for event in events if event.get("execution_outcome") == "failed" or ("execution_outcome" not in event and event.get("success") is False)]
    elif filter_name == "installs":
        events = [event for event in events if "package_install" in str(event.get("tool_name", ""))]
    elif filter_name == "sandbox":
        events = [event for event in events if "sandbox_run" in str(event.get("tool_name", ""))]
    elif filter_name == "approvals":
        events = [event for event in events if event.get("permission_decision") not in {None, "host-default"}]
    last_n = args.get("last_n", 20)
    return {"events": events[-last_n:], "matched": len(events), "scope": "persistent_history"}


async def handle_mcp_audit(args: dict[str, Any]) -> dict[str, Any]:
    project_dir, error = _resolve_dir(args.get("cwd"))
    if error:
        return {"status": "invalid_input", "error": error}
    assert project_dir is not None
    include_host = args.get("include_host") is True
    configs = []
    for path in _mcp_config_paths(project_dir, include_host=include_host, extra=args.get("config_path")):
        project_root = project_dir if _is_lexically_project_local(path, project_dir) else None
        summary = _summarize_mcp_file(path, project_root=project_root)
        if summary is not None:
            configs.append(summary)
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
    method = request.get("method")
    request_id = request.get("id")
    if not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "request method must be a string"}}
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
        params = request.get("params")
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "tools/call params must be an object"}}
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "tool name must be a string"}}
        handler = HANDLERS.get(tool_name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        tool_args, validation_error = validate_tool_arguments(tool_name, params.get("arguments", {}))
        if validation_error or tool_args is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": validation_error or "invalid tool arguments"}}
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
