"""Environment cache use must require an established mutation-generation authority."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from src.discovery.discovery import EnvironmentDiscovery
from src.models.environment import EnvironmentSnapshot


def _snapshot(label: str) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(timestamp=datetime.now(), success=False, errors=[label])


def test_generation_token_distinguishes_absent_baseline_from_malformed_state(tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)

    assert discovery._generation() == ""

    discovery.cache_dir.mkdir(parents=True, exist_ok=True)
    token = "a" * 32
    discovery.generation_file.write_text(token, encoding="ascii")
    assert discovery._generation() == token

    for invalid in (b"", b"unknown", b"g" * 32, b"a" * 33, b"\xff" * 32):
        discovery.generation_file.write_bytes(invalid)
        assert discovery._generation() is None


def test_unestablished_generation_bypasses_existing_cache_and_does_not_publish(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    discovery.cache_dir.mkdir(parents=True, exist_ok=True)
    cached = _snapshot("cached")
    fresh = _snapshot("fresh")
    discovery.cache_file.write_text(json.dumps(cached.to_dict()), encoding="utf-8")
    original_cache = discovery.cache_file.read_bytes()
    discovery.generation_file.write_text("malformed-generation", encoding="ascii")

    monkeypatch.setattr(discovery, "_run_discovery", lambda: fresh)

    result = discovery.discover()

    assert result is fresh
    assert discovery.cache_file.read_bytes() == original_cache
    assert discovery._generation() is None


def test_unreadable_generation_never_admits_cached_snapshot(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    fresh = _snapshot("fresh")
    monkeypatch.setattr(discovery, "_generation", lambda: None)
    monkeypatch.setattr(
        discovery,
        "_load_cache",
        lambda: (_ for _ in ()).throw(AssertionError("cache must not be read without generation authority")),
    )
    monkeypatch.setattr(discovery, "_run_discovery", lambda: fresh)

    assert discovery.discover() is fresh
    assert not discovery.cache_file.exists()


def test_generation_loss_before_publication_discards_temporary_cache(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    token = "b" * 32
    states = iter([token, None])
    monkeypatch.setattr(discovery, "_generation", lambda: next(states))

    discovery._save_cache(_snapshot("fresh"), expected_generation=token)

    assert not discovery.cache_file.exists()
    assert not list(tmp_path.glob("environment.json.*.tmp"))


def test_generation_loss_after_atomic_replace_removes_published_cache(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    token = "c" * 32
    states = iter([token, token, None])
    monkeypatch.setattr(discovery, "_generation", lambda: next(states))

    discovery._save_cache(_snapshot("fresh"), expected_generation=token)

    assert not discovery.cache_file.exists()
