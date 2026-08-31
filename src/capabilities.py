"""Small executable capability registry for Windows Dev Agent.

The catalog is JSON and uses only the Python standard library. Commands remain argv
vectors. Native targets execute directly; Windows PowerShell-script shims execute only
through the Windows-owned PowerShell interpreter, while .cmd/.bat targets are rejected.
Command-target identity is resolved once and carried into execution.

Host permission is deliberately not represented as a model-supplied field. An
MCP client requests execution with ``execute=true``; Claude Code or Codex then
owns any human approval prompt around that exact call. The runtime still blocks
forbidden capabilities and preserves the effective safety class in its result.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping, Optional

from src.execution import executable_identity_matches, resolve_executable, run_bounded
from src.file_guard import executable_identity, valid_executable_identity

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPABILITIES_FILE = ROOT / "capabilities.json"
VALID_SAFETY = {"read-only", "reversible", "approval-required", "forbidden"}


class CapabilityConfigError(ValueError):
    """Raised when the runtime capability catalog is malformed."""


@dataclass(frozen=True)
class CapabilityTool:
    name: str
    argv: tuple[str, ...]

    @property
    def executable(self) -> str:
        return self.argv[0]


@dataclass(frozen=True)
class Capability:
    id: str
    description: str
    safety: str
    tools: tuple[CapabilityTool, ...]
    tags: tuple[str, ...] = ()

    @property
    def requires_approval(self) -> bool:
        return self.safety in {"reversible", "approval-required"}

    @property
    def forbidden(self) -> bool:
        return self.safety == "forbidden"


def _argv(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CapabilityConfigError(f"{field} must be a non-empty list")
    if not all(isinstance(item, str) and item for item in value):
        raise CapabilityConfigError(f"{field} entries must be non-empty strings")
    return tuple(value)


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CapabilityConfigError(f"{field} must be a list of strings")
    return tuple(value)


def load_capabilities(path: Optional[Path] = None) -> dict[str, Capability]:
    """Load and validate the runtime capability catalog."""
    path = path or DEFAULT_CAPABILITIES_FILE
    if not path.exists():
        raise CapabilityConfigError(f"Capability file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityConfigError(f"Capability file must be valid JSON: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise CapabilityConfigError("Capability catalog must contain an object mapping")

    capabilities: dict[str, Capability] = {}
    for cap_id, spec in raw.items():
        if not isinstance(cap_id, str) or not cap_id:
            raise CapabilityConfigError("Capability IDs must be non-empty strings")
        if not isinstance(spec, Mapping):
            raise CapabilityConfigError(f"Capability {cap_id!r} must be an object")

        description = spec.get("description", "")
        if not isinstance(description, str) or not description:
            raise CapabilityConfigError(f"Capability {cap_id!r} needs a description")

        safety = spec.get("safety", "approval-required")
        if safety not in VALID_SAFETY:
            raise CapabilityConfigError(
                f"Capability {cap_id!r} has invalid safety {safety!r}; "
                f"expected one of {sorted(VALID_SAFETY)}"
            )

        raw_tools = spec.get("tools", [])
        if not isinstance(raw_tools, list) or not raw_tools:
            raise CapabilityConfigError(f"Capability {cap_id!r} needs at least one tool")

        tools: list[CapabilityTool] = []
        for index, tool in enumerate(raw_tools):
            if not isinstance(tool, Mapping):
                raise CapabilityConfigError(f"{cap_id}.tools[{index}] must be an object")
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                raise CapabilityConfigError(f"{cap_id}.tools[{index}].name is required")
            tools.append(
                CapabilityTool(
                    name=name,
                    argv=_argv(tool.get("argv"), field=f"{cap_id}.{name}.argv"),
                )
            )

        capabilities[cap_id] = Capability(
            id=cap_id,
            description=description,
            safety=safety,
            tools=tuple(tools),
            tags=_string_list(spec.get("tags"), field=f"{cap_id}.tags"),
        )

    return capabilities


def select_available_tool(capability: Capability) -> Optional[CapabilityTool]:
    """Return the first configured tool with a resolved executable identity."""
    for tool in capability.tools:
        executable = resolve_executable(tool.executable)
        if executable:
            return CapabilityTool(name=tool.name, argv=(executable, *tool.argv[1:]))
    return None


def command_display(argv: Iterable[str]) -> str:
    """Render an argv vector for human review without changing execution semantics."""
    return subprocess.list2cmdline(list(argv))


def effective_safety(capability: Capability, extra_args: list[str]) -> str:
    """Return the authority class for the concrete capability request."""
    if capability.forbidden:
        return "forbidden"
    if extra_args:
        return "approval-required"
    return capability.safety


def run_capability(
    capability_id: str,
    *,
    execute: bool = False,
    extra_args: Optional[list[str]] = None,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    expected_executable: Optional[str] = None,
    expected_executable_identity_kind: Optional[str] = None,
    expected_executable_identity_sha256: Optional[str] = None,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Plan or execute a configured capability under host-owned approval."""
    capabilities = load_capabilities(path)
    capability = capabilities.get(capability_id)
    if capability is None:
        return {
            "status": "unknown_capability",
            "capability": capability_id,
            "available": sorted(capabilities),
        }

    tool = select_available_tool(capability)
    if tool is None:
        return {
            "status": "unavailable",
            "capability": capability.id,
            "description": capability.description,
            "safety_class": capability.safety,
            "checked_tools": [configured.name for configured in capability.tools],
        }

    extra_args = extra_args or []
    if not isinstance(extra_args, list) or not all(isinstance(arg, str) and arg for arg in extra_args):
        return {"status": "invalid_input", "error": "extra_args must be a list of non-empty strings"}

    safety_class = effective_safety(capability, extra_args)
    argv = [*tool.argv, *extra_args]
    identity = executable_identity(tool.argv[0])
    plan: dict[str, Any] = {
        "status": "planned",
        "capability": capability.id,
        "description": capability.description,
        "base_safety_class": capability.safety,
        "safety_class": safety_class,
        "tool": tool.name,
        "executable": tool.argv[0],
        "executable_identity_kind": identity.kind if identity else None,
        "executable_identity_sha256": identity.sha256 if identity else None,
        "argv": argv,
        "command": command_display(argv),
        "requires_host_approval": safety_class in {"reversible", "approval-required"},
    }

    if identity is None:
        return {
            **plan,
            "status": "unavailable",
            "error": "Executable identity could not be established",
            "execution_started": False,
        }
    if not execute:
        return plan
    if safety_class == "forbidden":
        return {**plan, "status": "blocked", "error": "Capability is forbidden", "execution_started": False}
    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, tool.argv[0]):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }
    if not valid_executable_identity(expected_executable_identity_kind, expected_executable_identity_sha256):
        return {
            **plan,
            "status": "invalid_input",
            "error": "reviewed executable identity kind and fingerprint are required for execution",
            "execution_started": False,
        }
    if (
        identity.kind != expected_executable_identity_kind
        or identity.sha256 != expected_executable_identity_sha256.lower()
    ):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Executable identity no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    run_cwd: Optional[Path] = None
    if cwd:
        run_cwd = Path(cwd).expanduser().resolve()
        if not run_cwd.is_dir():
            return {**plan, "status": "invalid_input", "error": f"cwd is not a directory: {run_cwd}", "execution_started": False}

    result = run_bounded(
        argv,
        cwd=run_cwd,
        timeout=max(1, min(int(timeout_seconds), 600)),
        expected_executable_identity_kind=expected_executable_identity_kind,
        expected_executable_identity_sha256=expected_executable_identity_sha256.lower(),
    )
    if result.get("identity_mismatch") is True:
        return {
            **plan,
            **result,
            "status": "stale_plan",
            "error": "Executable identity changed after plan validation; obtain a fresh plan before execution",
        }
    return {
        **plan,
        **result,
        "status": "completed" if result.get("succeeded") else "failed",
    }
