"""Keep external process creation owned by src.execution.

The shared runner is where command-target resolution, typed identity sealing,
timeouts, bounded output, execution-start accounting, and Windows process-tree
cleanup compose. Runtime modules may use harmless subprocess helpers/constants,
but they must not create or replace processes outside that owner.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
EXECUTION_OWNER = SRC / "execution.py"

_FORBIDDEN_BY_MODULE = {
    "subprocess": {
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    },
    "os": {
        "system",
        "popen",
        "startfile",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
    },
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}


def _external_launch_calls(source: str, *, filename: str = "<source>") -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    module_aliases: dict[str, str] = {}
    direct_aliases: dict[str, tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name in _FORBIDDEN_BY_MODULE:
                    module_aliases[item.asname or item.name] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module in _FORBIDDEN_BY_MODULE:
            forbidden = _FORBIDDEN_BY_MODULE[node.module]
            for item in node.names:
                if item.name in forbidden:
                    direct_aliases[item.asname or item.name] = (node.module, item.name)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = module_aliases.get(func.value.id)
            if module and func.attr in _FORBIDDEN_BY_MODULE[module]:
                found.append((node.lineno, f"{module}.{func.attr}"))
        elif isinstance(func, ast.Name) and func.id in direct_aliases:
            module, name = direct_aliases[func.id]
            found.append((node.lineno, f"{module}.{name}"))
    return sorted(found)


def test_launch_scanner_catches_module_and_direct_aliases():
    source = """
import subprocess as sp
from os import system as host_system
import asyncio
sp.Popen(["x"])
host_system("x")
asyncio.create_subprocess_shell("x")
subprocess_like = object()
subprocess_like.run()
"""
    assert _external_launch_calls(source) == [
        (5, "subprocess.Popen"),
        (6, "os.system"),
        (7, "asyncio.create_subprocess_shell"),
    ]


def test_runtime_has_one_external_process_creation_owner():
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == EXECUTION_OWNER:
            continue
        source = path.read_text(encoding="utf-8")
        for line, call in _external_launch_calls(source, filename=str(path)):
            violations.append(f"{path.relative_to(ROOT)}:{line}: {call}")

    assert not violations, (
        "External process creation bypasses src/execution.py; route the launch through "
        "the identity-sealed runner instead:\n" + "\n".join(violations)
    )
