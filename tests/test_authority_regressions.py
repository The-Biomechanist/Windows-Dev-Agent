"""Regressions for authority state that must be established before execution/routing."""

from pathlib import Path

from src.file_guard import ExecutableIdentity
from src.mcp import server


def test_package_install_refuses_execution_when_cache_invalidation_fails(monkeypatch, tmp_path: Path):
    trusted = r"C:\trusted\winget.exe"
    identity = ExecutableIdentity(kind="file", sha256="a" * 64)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: False)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("installer must not start without cache invalidation")),
    )

    result = __import__("asyncio").run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "source": "winget",
                "execute": True,
                "expected_executable": trusted,
                "expected_executable_identity_kind": identity.kind,
                "expected_executable_identity_sha256": identity.sha256,
            }
        )
    )

    assert result["status"] == "failed"
    assert result["execution_started"] is False
    assert result["environment_cache_invalidated"] is False
    assert "cache" in result["error"].lower()


def test_devcontainer_route_requires_actual_configuration_file(monkeypatch, tmp_path: Path):
    (tmp_path / ".devcontainer").mkdir()
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\trusted\devcontainer.exe" if name == "devcontainer" else None)

    environment, error, status = server._select_sandbox(
        "auto", "project_reproducibility", tmp_path.resolve()
    )
    assert environment is None
    assert status == "unavailable"
    assert "configured Dev Container" in (error or "")

    config = tmp_path / ".devcontainer" / "devcontainer.json"
    config.write_text('{"image":"example"}', encoding="utf-8")
    environment, error, status = server._select_sandbox(
        "auto", "project_reproducibility", tmp_path.resolve()
    )
    assert environment == "dev_container"
    assert error is None
    assert status is None


def test_devcontainer_route_rejects_project_reparse_configuration(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / ".devcontainer"
    config_dir.mkdir()
    config = config_dir / "devcontainer.json"
    config.write_text('{"image":"example"}', encoding="utf-8")
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\trusted\devcontainer.exe" if name == "devcontainer" else None)

    original = server._project_path_status

    def fake_status(project_root: Path, path: Path):
        if path == config:
            return False, f"project path crosses a symbolic link or reparse point: {path}"
        return original(project_root, path)

    monkeypatch.setattr(server, "_project_path_status", fake_status)
    environment, error, status = server._select_sandbox(
        "auto", "project_reproducibility", tmp_path.resolve()
    )

    assert environment is None
    assert status == "invalid_input"
    assert "reparse" in (error or "").lower()
