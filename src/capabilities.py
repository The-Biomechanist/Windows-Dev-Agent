"""Small executable capability registry for Windows Dev Agent.

The catalog uses the JSON-compatible subset of YAML and the Python standard
library. Commands are argv vectors and never execute through a host shell.
Safety is computed for the effective request: appended caller arguments cannot
inherit a weaker auto-allowable classification from the base capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPABILITIES_FILE = ROOT / "capabilities.yaml"
VALID_SAFETY = {"read-only", "reversible", "approval-required", "forbidden"}


class CapabilityConfigError(ValueError):
    """Raised when the runtime capability catalog is malformed."""


@dataclass(frozen=True)
class CapabilityTool:
    name: str
    argv: tuple[str, ...]
    check_argv: tuple[str, ...] = ()
    verify_argv: tuple[str, ...] = ()
    rollback_argv: tuple[str, ...] = ()

    @property
    def executable(self) -> str:
        return (self.check_argv or self.argv)[0]


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


def _argv(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None and allow_empty:
        return ()
    if not isinstance(value, list) or (not value and not allow_empty):
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
        raise CapabilityConfigError(
            f"Capability file must use JSON-compatible YAML syntax: {exc}"
        ) from exc

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
                    check_argv=_argv(
                        tool.get("check_argv"),
                        field=f"{cap_id}.{name}.check_argv",
                        allow_empty=True,
                    ),
                    verify_argv=_argv(
                        tool.get("verify_argv"),
                        field=f"{cap_id}.{name}.verify_argv",
                        allow_empty=True,
                    ),
                    rollback_argv=_argv(
                        tool.get("rollback_argv"),
                        field=f"{cap_id}.{name}.rollback_argv",
                        allow_empty=True,
                    ),
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
    """Return the first configured tool whose executable is currently present."""
    for tool in capability.tools:
        if shutil.which(tool.executable):
            return tool
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
    user_approved: bool = False,
    extra_args: Optional[list[str]] = None,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Plan or execute a configured capability.

    The Claude Code hook is the host permission authority. The server also
    requires an explicit acknowledgement for reversible and approval-required
    execution so direct MCP use cannot silently bypass the same boundary.
    Child processes receive DEVNULL stdin so they cannot consume the MCP stdio
    transport if they unexpectedly become interactive.
    """
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
            "checked_tools": [tool.name for tool in capability.tools],
        }

    extra_args = extra_args or []
    if not isinstance(extra_args, list) or not all(isinstance(arg, str) and arg for arg in extra_args):
        return {"status": "invalid_input", "error": "extra_args must be a list of non-empty strings"}

    safety_class = effective_safety(capability, extra_args)
    argv = [*tool.argv, *extra_args]
    requires_user_approval = safety_class in {"reversible", "approval-required"}
    plan: dict[str, Any] = {
        "status": "planned",
        "capability": capability.id,
        "description": capability.description,
        "base_safety_class": capability.safety,
        "safety_class": safety_class,
        "tool": tool.name,
        "argv": argv,
        "command": command_display(argv),
        "verify_argv": list(tool.verify_argv),
        "rollback_argv": list(tool.rollback_argv),
        "requires_user_approval": requires_user_approval,
    }

    if not execute:
        return plan
    if safety_class == "forbidden":
        return {**plan, "status": "blocked", "error": "Capability is forbidden"}
    if requires_user_approval and not user_approved:
        return {
            **plan,
            "status": "approval_required",
            "error": "Host approval acknowledgement is required for this effective capability request",
        }

    run_cwd: Optional[Path] = None
    if cwd:
        run_cwd = Path(cwd).expanduser().resolve()
        if not run_cwd.is_dir():
            return {**plan, "status": "invalid_input", "error": f"cwd is not a directory: {run_cwd}"}

    try:
        result = subprocess.run(
            argv,
            cwd=str(run_cwd) if run_cwd else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout_seconds), 600)),
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**plan, "status": "failed", "error": str(exc)}

    return {
        **plan,
        "status": "completed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "succeeded": result.returncode == 0,
    }
