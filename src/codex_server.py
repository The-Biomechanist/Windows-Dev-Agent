"""Codex host adapter for the shared Windows Dev Agent MCP runtime.

The shared server owns Windows behavior. This adapter owns only Codex-specific
host bindings:

- stable writable plugin data outside the installed plugin cache;
- explicit project-directory requirements for project-scoped tools, because a
  bundled plugin MCP server starts in the plugin cache rather than the user's
  active project;
- Codex-facing MCP instructions and tool schemas.

Permission prompts remain Codex-owned through the plugin MCP approval policy.
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

from src.runtime_paths import resolve_data_dir

os.environ.setdefault("WINDOWS_DEV_AGENT_HOST", "codex")
os.environ.setdefault(
    "WINDOWS_DEV_AGENT_DATA_DIR",
    str(resolve_data_dir(host="codex")),
)

from src.mcp import server as common  # noqa: E402

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
def _project_binding(project: Path) -> Iterator[None]:
    key = "WINDOWS_DEV_AGENT_PROJECT_DIR"
    previous = os.environ.get(key)
    os.environ[key] = str(project)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _invalid_tool_result(request_id: Any, error: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"status": "invalid_input", "error": error},
                        indent=2,
                    ),
                }
            ]
        },
    }


async def handle_request(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = request.get("method", "")
    request_id = request.get("id")

    if method == "initialize":
        response = await common.handle_request(request)
        if response and isinstance(response.get("result"), dict):
            response["result"]["instructions"] = CODEX_INSTRUCTIONS
            response["result"]["serverInfo"] = {
                "name": "windows-dev-agent",
                "version": "0.3.0",
            }
        return response

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS},
        }

    if method != "tools/call":
        return await common.handle_request(request)

    params = request.get("params") or {}
    tool_name = str(params.get("name", ""))
    project_arg = PROJECT_ARG_BY_TOOL.get(tool_name)
    if not project_arg:
        return await common.handle_request(request)

    tool_args = params.get("arguments") or {}
    if not isinstance(tool_args, dict):
        return _invalid_tool_result(request_id, "tool arguments must be an object")

    project, error = _resolve_project(tool_args.get(project_arg))
    if error or project is None:
        return _invalid_tool_result(request_id, error or "project directory is required")

    with _project_binding(project):
        return await common.handle_request(request)


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
