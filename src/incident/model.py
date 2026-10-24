"""Plan model for incident response: validation, digests, and plain-language review.

A plan is the only way the incident tools change the machine. Validation runs
before every execution, so a stale, altered, or out-of-scope plan cannot run.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import ipaddress
import json
import re
from typing import Any, Callable

PLAN_VERSION = 1
MAX_STEPS = 12
MAX_REASON_CHARS = 300
EVIDENCE_MAX_AGE = timedelta(minutes=30)
RUN_HIVES = ("HKCU", "HKLM")
PLAN_KEYS = frozenset({"version", "evidence_id", "goal", "steps", "digest"})
STEP_KEYS = frozenset({"id", "action", "target", "reason"})

# Every action names the one reversible primitive it uses. There is deliberately
# no action for deleting files or keys, changing security tooling, or sending data.
ACTIONS: dict[str, dict[str, Any]] = {
    "quarantine_file": {
        "fields": ("path",),
        "undo": "move the file back to its original location",
    },
    "stop_process": {
        "fields": ("pid", "path", "name", "started"),
        "undo": "none; the process is stopped and must be started again by hand",
    },
    "disable_run_value": {
        "fields": ("hive", "name"),
        "undo": "write the startup value back exactly as it was",
    },
    "disable_scheduled_task": {
        "fields": ("task_path", "task_name"),
        "undo": "enable the scheduled task again",
    },
    "block_program_outbound": {
        "fields": ("path",),
        "undo": "remove the firewall rule",
    },
    "block_remote_address": {
        "fields": ("address",),
        "undo": "remove the firewall rule",
    },
}

PROTECTED_ROOTS = (
    "c:\\windows\\",
    "c:\\program files\\",
    "c:\\program files (x86)\\",
    "c:\\programdata\\microsoft\\windows defender\\",
)
PROTECTED_PROCESS_NAMES = frozenset(
    {
        "system", "idle", "smss", "csrss", "wininit", "winlogon", "services", "lsass",
        "lsaiso", "svchost", "msmpeng", "nissrv", "sihost", "dwm", "fontdrvhost",
    }
)
USER_WRITABLE_MARKERS = ("\\appdata\\", "\\temp\\", "\\downloads\\", "\\users\\public\\")

_DRIVE_PATH = re.compile(r"^[A-Za-z]:\\[^\"<>|?*/]+$")
_SAFE_NAME = re.compile(r"^[^\\/*?\[\]\"<>|\x00-\x1f]{1,255}$")


def is_protected_path(value: str) -> bool:
    return value.lower().startswith(PROTECTED_ROOTS)


def path_problem(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 260 or not _DRIVE_PATH.match(value):
        return "path must be an absolute local drive path such as C:\\folder\\file.exe"
    if ".." in value.split("\\"):
        return "path must not contain '..' segments"
    if is_protected_path(value):
        return "path is in a protected Windows location"
    return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def plan_digest(plan: dict[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "digest"}
    return hashlib.sha256(canonical_json(body).encode("ascii")).hexdigest()


TRUSTED_REFUSAL = "signed by a trusted publisher; plans act only on unsigned or unverifiable items"


def _only_trusted(matches: list[dict[str, Any]]) -> bool:
    return bool(matches) and all(m.get("signed") is True for m in matches)


def _check_quarantine(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    problem = path_problem(target["path"])
    if problem:
        return [problem]
    matches = [
        item for item in (*processes, *persistence) if _text(item.get("path")) == target["path"]
    ]
    if not matches:
        return ["path was not observed in the latest evidence"]
    return [f"file is {TRUSTED_REFUSAL}"] if _only_trusted(matches) else []


def _check_stop(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    problems: list[str] = []
    pid = target["pid"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        problems.append("pid must be a positive integer")
    name = target["name"]
    if not isinstance(name, str) or name.lower() in PROTECTED_PROCESS_NAMES:
        problems.append("refusing to stop a protected system process")
    problem = path_problem(target["path"])
    if problem:
        problems.append(problem)
    matches = [
        p for p in processes
        if p.get("pid") == pid
        and _text(p.get("path")) == target["path"]
        and p.get("name") == name
        and p.get("started") == target["started"]
    ]
    if not matches:
        problems.append("process was not observed in the latest evidence with that pid, name, path, and start time")
    elif _only_trusted(matches):
        problems.append(f"process is {TRUSTED_REFUSAL}")
    return problems


def _check_run_value(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    if target["hive"] not in RUN_HIVES:
        return ["hive must be HKCU or HKLM"]
    name = target["name"]
    if not isinstance(name, str) or not _SAFE_NAME.match(name):
        return ["name must be a plain value name without wildcards"]
    matches = [
        e for e in persistence
        if e.get("kind") == "run_key" and e.get("hive") == target["hive"] and e.get("name") == name
    ]
    if not matches:
        return ["startup value was not observed in the latest evidence"]
    return [f"startup entry is {TRUSTED_REFUSAL}"] if _only_trusted(matches) else []


def _check_scheduled_task(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    problems: list[str] = []
    task_path = target["task_path"]
    if not isinstance(task_path, str) or not task_path.startswith("\\") or not task_path.endswith("\\"):
        problems.append("task_path must look like \\Folder\\")
    elif task_path.lower().startswith("\\microsoft\\"):
        problems.append("built-in Windows scheduled tasks are never disabled")
    task_name = target["task_name"]
    if not isinstance(task_name, str) or not _SAFE_NAME.match(task_name):
        problems.append("task_name must be a plain task name without wildcards")
    matches = [
        e for e in persistence
        if e.get("kind") == "scheduled_task" and e.get("task_path") == task_path and e.get("name") == task_name
    ]
    if not matches:
        problems.append("scheduled task was not observed in the latest evidence")
    elif _only_trusted(matches):
        problems.append(f"scheduled task is {TRUSTED_REFUSAL}")
    return problems


def _check_block_program(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    problem = path_problem(target["path"])
    if problem:
        return [problem]
    matches = [
        p for p in processes
        if _text(p.get("path")) == target["path"] and isinstance(p.get("connections"), int) and p["connections"] > 0
    ]
    if not matches:
        return ["program has no observed network connections, so blocking it is not justified"]
    return [f"program is {TRUSTED_REFUSAL}"] if _only_trusted(matches) else []


def _check_block_address(target: dict[str, Any], processes: list, persistence: list) -> list[str]:
    if not isinstance(target["address"], str):
        return ["address must be an IP address"]
    try:
        address = ipaddress.ip_address(target["address"])
    except ValueError:
        return ["address must be an IP address"]
    if not address.is_global:
        return ["private, loopback, link-local, multicast, and reserved addresses are never blocked"]
    observed = {str(r) for p in processes for r in (p.get("remote") or []) if isinstance(r, str)}
    return [] if str(address) in observed else ["address was not observed in the latest evidence"]


_CHECKERS: dict[str, Callable[[dict[str, Any], list, list], list[str]]] = {
    "quarantine_file": _check_quarantine,
    "stop_process": _check_stop,
    "disable_run_value": _check_run_value,
    "disable_scheduled_task": _check_scheduled_task,
    "block_program_outbound": _check_block_program,
    "block_remote_address": _check_block_address,
}


def _step_errors(number: int, step: Any, ids: set[str], processes: list, persistence: list) -> list[str]:
    label = f"step {number}"
    if not isinstance(step, dict):
        return [f"{label}: must be an object"]
    problems: list[str] = []
    unknown = set(step) - STEP_KEYS
    if unknown:
        problems.append(f"{label}: unknown field(s) {sorted(unknown)}")
    step_id = step.get("id")
    if not isinstance(step_id, str) or not step_id or len(step_id) > 40:
        problems.append(f"{label}: id must be a short string")
    elif step_id in ids:
        problems.append(f"{label}: duplicate id {step_id}")
    else:
        ids.add(step_id)
    action = step.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        return problems + [f"{label}: action {action!r} is not allowed; allowed actions are {sorted(ACTIONS)}"]
    reason = step.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON_CHARS:
        problems.append(f"{label}: reason must explain the step in under {MAX_REASON_CHARS} characters")
    target = step.get("target")
    if not isinstance(target, dict):
        return problems + [f"{label}: target must be an object"]
    missing = [field for field in ACTIONS[action]["fields"] if field not in target]
    if missing:
        return problems + [f"{label}: target is missing {missing}"]
    return problems + [f"{label}: {p}" for p in _CHECKERS[action](target, processes, persistence)]


def validate_plan(plan: Any, record: dict[str, Any], now: datetime) -> list[str]:
    """Return every reason the plan may not run. An empty list means it may."""
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    errors: list[str] = []
    unknown = set(plan) - PLAN_KEYS
    if unknown:
        errors.append(f"unknown plan field(s) {sorted(unknown)}")
    if plan.get("version") != PLAN_VERSION:
        errors.append(f"plan version must be {PLAN_VERSION}")
    if plan.get("evidence_id") != record.get("evidence_id"):
        errors.append("plan refers to different evidence than the one it was checked against")
    collected = datetime.fromisoformat(record["collected_at"])
    if now - collected > EVIDENCE_MAX_AGE:
        errors.append("evidence is older than 30 minutes; collect again before planning")
    goal = plan.get("goal")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 300:
        errors.append("goal must be a short description of what the plan is for")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        errors.append(f"steps must be a list of 1 to {MAX_STEPS} steps")
        return errors
    snapshot = record["snapshot"]
    processes = [p for p in snapshot["processes"] if isinstance(p, dict)]
    persistence = [e for e in snapshot["persistence"] if isinstance(e, dict)]
    ids: set[str] = set()
    for number, step in enumerate(steps, start=1):
        errors.extend(_step_errors(number, step, ids, processes, persistence))
    return errors


def render_plan(plan: dict[str, Any]) -> list[str]:
    """Plain-language sentences a person can read before approving."""
    sentences = []
    for number, step in enumerate(plan["steps"], start=1):
        target = step["target"]
        action = step["action"]
        if action == "quarantine_file":
            what = f"Move {target['path']} into the incident quarantine folder (kept, not deleted)"
        elif action == "stop_process":
            what = f"Stop process {target['name']} (pid {target['pid']}) running from {target['path']}"
        elif action == "disable_run_value":
            what = f"Remove the startup entry '{target['name']}' from {target['hive']} Run (its value is saved first)"
        elif action == "disable_scheduled_task":
            what = f"Disable scheduled task {target['task_path']}{target['task_name']} (not deleted)"
        elif action == "block_program_outbound":
            what = f"Block outbound network access for {target['path']} with a firewall rule"
        else:
            what = f"Block outbound traffic to {target['address']} with a firewall rule"
        undo = ACTIONS[action]["undo"]
        sentences.append(f"{number}. {what}. Undo: {undo}. Why: {step['reason']}")
    return sentences


def summarize_request(tool_input: Any) -> str:
    """A one-line description of an execute request, shown in the approval prompt."""
    plan = tool_input.get("plan") if isinstance(tool_input, dict) else None
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return "No valid plan was supplied."
    actions = [str(s.get("action")) for s in plan["steps"] if isinstance(s, dict)]
    counts = Counter(actions)
    parts = ", ".join(f"{count} x {name}" for name, count in sorted(counts.items()))
    return f"Plan {plan_digest(plan)[:12]} with {len(actions)} step(s): {parts}. Review every step before approving."
