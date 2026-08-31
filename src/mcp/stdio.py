"""One bounded JSON-line stdio transport shared by Windows Dev Agent host adapters."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Awaitable, Callable, Optional

MAX_MCP_REQUEST_BYTES = 256 * 1024

RequestHandler = Callable[[dict[str, Any]], Awaitable[Optional[dict[str, Any]]]]


def _error(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _readline_bounded() -> tuple[Optional[bytes], Optional[str]]:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    chunk = stream.readline(MAX_MCP_REQUEST_BYTES + 1)
    if not chunk:
        return None, None
    if isinstance(chunk, str):
        raw = chunk.encode("utf-8")
    else:
        raw = bytes(chunk)
    if len(raw) <= MAX_MCP_REQUEST_BYTES:
        return raw, None

    # Drain the remainder of this JSON-line frame before accepting the next one.
    while not raw.endswith(b"\n"):
        remainder = stream.readline(MAX_MCP_REQUEST_BYTES + 1)
        if not remainder:
            break
        if isinstance(remainder, str):
            raw = remainder.encode("utf-8")
        else:
            raw = bytes(remainder)
    return b"", f"MCP request exceeds {MAX_MCP_REQUEST_BYTES} byte limit"


def run_stdio(handler: RequestHandler, *, logger: Optional[logging.Logger] = None) -> int:
    """Run one request-at-a-time MCP JSON-line loop with a hard input bound."""
    log = logger or logging.getLogger(__name__)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while True:
            raw, read_error = _readline_bounded()
            if raw is None:
                break
            if read_error:
                response = _error(-32600, read_error)
            else:
                if not raw.strip():
                    continue
                try:
                    request = json.loads(raw.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("request must be a JSON object")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    response = _error(-32700, str(exc))
                else:
                    try:
                        response = loop.run_until_complete(handler(request))
                    except Exception as exc:
                        log.exception("MCP adapter request failed")
                        response = _error(-32000, str(exc), request.get("id"))
            if response is not None:
                sys.stdout.write(json.dumps(response, default=str, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        loop.close()
    return 0
