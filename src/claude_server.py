"""Claude Code host adapter for the shared Windows Dev Agent MCP runtime.

Claude supplies the active project root through ``WINDOWS_DEV_AGENT_PROJECT_DIR``.
Project-scoped tool arguments may select that directory or a descendant, but may
not replace the host-owned project boundary with an arbitrary filesystem path.
Relative project paths are interpreted under the host-owned project root.
"""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from src import __version__
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


def _claude_project_root() -> tuple[Optional[Path], Optional[str]]:
    raw = os.environ.get("WINDOWS_DEV_AGENT_PROJECT_DIR", "").strip()
    if not raw:
        return None, "Claude project directory was not supplied by the host"
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        return None, f"Claude project directory is not a directory: {root}"
    return root, None


def _bind_project_scope(request: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if request.get("method") != "tools/call":
        return request, None

    params = request.get("params")
    if not isinstance(params, dict):
        return None, "tool params must be an object"
    tool_name = params.get("name")
    if not isinstance(tool_name, str):
        return None, "tool name must be a string"
    project_arg = PROJECT_ARG_BY_TOOL.get(tool_name)
    if project_arg is None:
        return request, None

    tool_args = params.get("arguments", {})
    if not isinstance(tool_args, dict):
        return None, "tool arguments must be an object"

    root, error = _claude_project_root()
    if error or root is None:
        return None, error or "Claude project directory is required"

    raw_value = tool_args.get(project_arg, "")
    if raw_value is not None and not isinstance(raw_value, str):
        return None, f"{project_arg} must be a string"
    raw = (raw_value or "").strip()
    if raw:
        requested = Path(raw).expanduser()
        candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    else:
        candidate = root
    if not candidate.is_dir():
        return None, f"Not a directory: {candidate}"
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"{project_arg} must stay inside the active Claude project: {root}"

    normalized = deepcopy(request)
    normalized_params = normalized.setdefault("params", {})
    normalized_args = normalized_params.setdefault("arguments", {})
    normalized_args[project_arg] = str(candidate)
    return normalized, None


async def handle_request(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    normalized, error = _bind_project_scope(request)
    if error or normalized is None:
        return _invalid_tool_result(request.get("id"), error or "invalid project scope")

    response = await common.handle_request(normalized)
    if normalized.get("method") == "initialize" and response and isinstance(response.get("result"), dict):
        response["result"]["serverInfo"] = {"name": "windows-dev-agent", "version": __version__}
    return response


def main_sync() -> int:
    common._cleanup_stale_sandbox_bundles()
    return run_stdio(handle_request, logger=logger)


if __name__ == "__main__":
    raise SystemExit(main_sync())
