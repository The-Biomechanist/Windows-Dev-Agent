"""Focused package-manager execution contracts."""

import asyncio

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_winget_install_plan_binds_reviewed_source_and_disables_interaction():
    plan = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "source": "winget", "execute": False}
        )
    )

    assert plan["status"] == "planned"
    source_index = plan["argv"].index("--source")
    assert plan["argv"][source_index + 1] == "winget"
    assert "--accept-package-agreements" in plan["argv"]
    assert "--accept-source-agreements" in plan["argv"]
    assert "--disable-interactivity" in plan["argv"]
