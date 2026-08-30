"""Resource-bound regressions for Windows Dev Agent acquisition surfaces."""

from pathlib import Path

from src.mcp import server


def test_payload_entry_budget_stops_directory_walk_early(tmp_path: Path, monkeypatch):
    folder = tmp_path / "payload"
    folder.mkdir()

    def guarded_rglob(self, _pattern):
        # The source directory itself consumes the one-entry budget. The first
        # child must trip the limit before the iterator is resumed again.
        yield self / "first"
        raise AssertionError("payload traversal continued after the entry budget was exceeded")

    monkeypatch.setattr(server, "MAX_SANDBOX_PAYLOAD_ENTRIES", 1)
    monkeypatch.setattr(Path, "rglob", guarded_rglob)
    sources, error = server._payload_sources(tmp_path, ["payload"])
    assert sources is None
    assert "entry budget" in error


def test_json_config_read_is_bounded_by_bytes_not_pre_read_stat(tmp_path: Path, monkeypatch):
    config = tmp_path / ".mcp.json"
    config.write_bytes(b'{"mcpServers":{}}' + b"x" * 32)
    monkeypatch.setattr(server, "MAX_JSON_CONFIG_BYTES", 8)

    # If _safe_json depended on a preliminary Path.stat size decision this
    # would fail before the bounded read. The reader should instead consume at
    # most limit+1 bytes and reject from the bytes actually acquired.
    monkeypatch.setattr(Path, "stat", lambda _self: (_ for _ in ()).throw(AssertionError("pre-read stat must not own the bound")))
    data, error = server._safe_json(config)
    assert data is None
    assert "read limit" in error
