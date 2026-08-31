"""Codex adapter for the shared Windows Dev Agent MCP runtime.

The shared server owns Windows behavior. This adapter owns only Codex-specific
persistent-data binding, explicit project identity, tool schemas, and optional
Codex host inventory. Human approval remains Codex-owned.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator, Optional

from src import __version__
from src.runtime_paths import codex_home, resolve_codex_data_dir
from src.mcp import server as common
from src.mcp.stdio import run_stdio

logger = logging.getLogger(__name__)

PROJECT_ARG_BY_TOOL = {
    "capability_run": "cwd",
    "workflow_plan": "cwd",
    "sandbox_run": "workspace_folder",
    "ecosystem_scan": "cwd",
    "mcp_audit": "cwd",
}

CODEX_INSTRUCTIONS = (
    "Windows Dev Agent Codex adapter. Pass the absolute current Codex project directory "
    "explicitly to project-scoped tools; never use the installed plugin cache as "
    "the project. Codex owns execution approval through MCP/shell policy. Bundled "
    "hook behavior is additional and is active only after the user trusts the "
    "plugin hooks; when trusted, those hooks independently bind project-scoped calls "
    "to the host event cwd. Without trusted hooks, the required absolute project path is "
    "caller-selected and prompt-visible rather than independently host-attested."
)


def _codex_tools() -> list[dict[str, Any]]:
    tools = deepcopy(common.TOOLS)
    for tool in tools:
        name = str(tool.get("name", ""))
        project_arg = PROJECT_ARG_BY_TOOL.get(name)
        if not project_arg:
            continue
        schema = tool.setdefault("inputSchema", {})
        properties = schema.setdefault("properties", {})
        properties.setdefault(
            project_arg,
            {
                "type": "string",
                "description": "Absolute current Codex session/project directory; never the installed plugin cache.",
            },
        )
        required = list(schema.get("required", []))
        if project_arg not in required:
            required.append(project_arg)
        schema["required"] = required
    return tools


TOOLS = _codex_tools()


def _resolve_project(value: Any) -> tuple[Optional[Path], Optional[str]]:
    if not isinstance(value, str):
        return None, "Current Codex project directory must be a string"
    raw = value.strip()
    if not raw:
        return None, "Current Codex project directory is required for this tool"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None, "Current Codex project directory must be an absolute path"
    path = path.resolve()
    if not path.is_dir():
        return None, f"Not a directory: {path}"
    return path, None


@contextmanager
def _runtime_binding(project: Optional[Path] = None) -> Iterator[None]:
    data_dir = resolve_codex_data_dir().expanduser()
    keys = ("WINDOWS_DEV_AGENT_HOST", "WINDOWS_DEV_AGENT_DATA_DIR", "WINDOWS_DEV_AGENT_PROJECT_DIR")
    previous_env = {key: os.environ.get(key) for key in keys}
    previous_common = (common.DATA_DIR, common.LOG_FILE)

    os.environ["WINDOWS_DEV_AGENT_HOST"] = "codex"
    os.environ["WINDOWS_DEV_AGENT_DATA_DIR"] = str(data_dir)
    if project is not None:
        os.environ["WINDOWS_DEV_AGENT_PROJECT_DIR"] = str(project)
    common.DATA_DIR = data_dir
    common.LOG_FILE = data_dir / "agent.log"
    try:
        yield
    finally:
        common.DATA_DIR, common.LOG_FILE = previous_common
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _invalid_tool_result(request_id: Any, error: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps({"status": "invalid_input", "error": error}, indent=2)}]},
    }


def _safe_names(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    try:
        return sorted(item.name for item in path.iterdir() if item.is_dir())
    except OSError:
        return []


def _codex_plugin_inventory() -> dict[str, Any]:
    home = codex_home()
    root = home / "plugins"
    cache = root / "cache"
    personal = [name for name in _safe_names(root) if name not in {"cache", "data"}]
    config = home / "config.toml"
    return {
        "root": str(root),
        "personal": personal,
        "cache_marketplaces": _safe_names(cache),
        "config_file": str(config) if config.is_file() else None,
    }


def _augment_ecosystem_response(response: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not response:
        return response
    try:
        content = response["result"]["content"]
        text_entry = next(item for item in content if item.get("type") == "text")
        payload = json.loads(text_entry["text"])
        inventory = payload["inventory"]
    except (KeyError, TypeError, StopIteration, json.JSONDecodeError):
        return response
    if inventory.get("host_inventory_included"):
        inventory["codex_plugins"] = _codex_plugin_inventory()
    text_entry["text"] = json.dumps(payload, indent=2, default=str)
    return response


async def handle_request(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = request.get("method", "")
    request_id = request.get("id")
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    project: Optional[Path] = None
    tool_name = ""
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return _invalid_tool_result(request_id, "tool params must be an object")
        raw_tool_name = params.get("name")
        if not isinstance(raw_tool_name, str):
            return _invalid_tool_result(request_id, "tool name must be a string")
        tool_name = raw_tool_name
        project_arg = PROJECT_ARG_BY_TOOL.get(tool_name)
        if project_arg:
            tool_args = params.get("arguments", {})
            if not isinstance(tool_args, dict):
                return _invalid_tool_result(request_id, "tool arguments must be an object")
            project, error = _resolve_project(tool_args.get(project_arg))
            if error or project is None:
                return _invalid_tool_result(request_id, error or "project directory is required")

    with _runtime_binding(project):
        response = await common.handle_request(request)

    if method == "initialize" and response and isinstance(response.get("result"), dict):
        response["result"]["instructions"] = CODEX_INSTRUCTIONS
        response["result"]["serverInfo"] = {"name": "windows-dev-agent", "version": __version__}
    if tool_name == "ecosystem_scan":
        response = _augment_ecosystem_response(response)
    return response


def main_sync() -> int:
    with _runtime_binding():
        common._cleanup_stale_sandbox_bundles()
    return run_stdio(handle_request, logger=logger)


if __name__ == "__main__":
    raise SystemExit(main_sync())
