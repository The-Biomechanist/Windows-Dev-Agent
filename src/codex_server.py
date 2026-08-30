"""Codex host adapter for the shared Windows Dev Agent MCP runtime.

The shared server owns Windows behavior. This adapter owns only Codex-specific
host bindings: persistent data, explicit project identity, Codex-facing schemas,
and Codex plugin/config inventory. Permission prompting remains Codex-owned.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Iterator, Optional

from src import __version__
from src.runtime_paths import codex_home, resolve_codex_data_dir
from src.mcp import server as common

logger = logging.getLogger(__name__)

PROJECT_ARG_BY_TOOL = {
    "capability_run": "cwd",
    "workflow_plan": "cwd",
    "sandbox_run": "workspace_folder",
    "ecosystem_scan": "cwd",
    "mcp_audit": "cwd",
}

CODEX_INSTRUCTIONS = (
    "Windows Dev Agent Codex adapter. For project-scoped tools, pass the current "
    "Codex session/project directory explicitly; never use the installed plugin "
    "cache as the project. Codex owns execution approval through its MCP tool "
    "approval policy. The MCP user_approved field is defense-in-depth "
    "acknowledgement only, not proof that permission was granted."
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
        properties[project_arg] = {
            "type": "string",
            "description": (
                "Current Codex session/project directory. Required by the Codex "
                "adapter because the plugin MCP process is rooted in the installed "
                "plugin cache, not the active project."
            ),
        }
        required = list(schema.get("required", []))
        if project_arg not in required:
            required.append(project_arg)
        schema["required"] = required
    return tools


TOOLS = _codex_tools()


def _resolve_project(value: Any) -> tuple[Optional[Path], Optional[str]]:
    raw = str(value or "").strip()
    if not raw:
        return None, "Current Codex project directory is required for this tool"
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        return None, f"Not a directory: {path}"
    return path, None


@contextmanager
def _runtime_binding(project: Optional[Path] = None) -> Iterator[None]:
    data_dir = resolve_codex_data_dir().expanduser()
    keys = ("WINDOWS_DEV_AGENT_HOST", "WINDOWS_DEV_AGENT_DATA_DIR", "WINDOWS_DEV_AGENT_PROJECT_DIR")
    previous_env = {key: os.environ.get(key) for key in keys}
    previous_common = (common.DATA_DIR, common.LOG_FILE, common.ENVIRONMENT_CACHE_FILE)

    os.environ["WINDOWS_DEV_AGENT_HOST"] = "codex"
    os.environ["WINDOWS_DEV_AGENT_DATA_DIR"] = str(data_dir)
    if project is not None:
        os.environ["WINDOWS_DEV_AGENT_PROJECT_DIR"] = str(project)

    common.DATA_DIR = data_dir
    common.LOG_FILE = data_dir / "agent.log"
    common.ENVIRONMENT_CACHE_FILE = data_dir / "environment.json"

    from src.discovery import discovery

    previous_discovery = (discovery.CACHE_DIR, discovery.CACHE_FILE)
    discovery.CACHE_DIR = data_dir
    discovery.CACHE_FILE = data_dir / "environment.json"
    try:
        yield
    finally:
        discovery.CACHE_DIR, discovery.CACHE_FILE = previous_discovery
        common.DATA_DIR, common.LOG_FILE, common.ENVIRONMENT_CACHE_FILE = previous_common
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _invalid_tool_result(request_id: Any, error: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"status": "invalid_input", "error": error}, indent=2),
                }
            ]
        },
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


def _augment_ecosystem_response(
    response: Optional[dict[str, Any]], project: Path
) -> Optional[dict[str, Any]]:
    if not response:
        return response
    try:
        content = response["result"]["content"]
        text_entry = next(item for item in content if item.get("type") == "text")
        payload = json.loads(text_entry["text"])
        inventory = payload["inventory"]
    except (KeyError, TypeError, StopIteration, json.JSONDecodeError):
        return response

    inventory["codex_plugins"] = _codex_plugin_inventory()
    agents_dir = project / ".agents"
    if agents_dir.exists():
        agent_configs = inventory.setdefault("agent_configs", [])
        agents_path = str(agents_dir)
        if agents_path not in agent_configs:
            agent_configs.append(agents_path)
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
        params = request.get("params") or {}
        tool_name = str(params.get("name", ""))
        project_arg = PROJECT_ARG_BY_TOOL.get(tool_name)
        if project_arg:
            tool_args = params.get("arguments") or {}
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
    if tool_name == "ecosystem_scan" and project is not None:
        response = _augment_ecosystem_response(response, project)
    return response


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
                logger.exception("Codex adapter request failed")
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": str(exc)},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, default=str) + "\n")
                sys.stdout.flush()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main_sync())
