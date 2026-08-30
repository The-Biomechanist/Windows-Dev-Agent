"""Claude Code host adapter for the shared Windows Dev Agent MCP runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Optional

from src import __version__
from src.mcp import server as common

logger = logging.getLogger(__name__)


async def handle_request(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    response = await common.handle_request(request)
    if request.get("method") == "initialize" and response and isinstance(response.get("result"), dict):
        response["result"]["serverInfo"] = {
            "name": "windows-dev-agent",
            "version": __version__,
        }
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
                logger.exception("Claude adapter request failed")
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
