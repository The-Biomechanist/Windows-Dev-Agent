"""Focused package-manager execution contracts."""

import asyncio

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_winget_install_plan_is_noninteractive_and_source_bound():
    plan = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "source": "winget", "execute": False}
        )
    )

    assert plan["status"] == "planned"
    assert "--source" in plan["argv"]
    assert plan["argv"][plan["argv"].index("--source") + 1] == "winget"
    assert "--accept-package-agreements" in plan["argv"]
    assert "--accept-source-agreements" in plan["argv"]
    assert "--disable-interactivity" in plan["argv"]
