"""Evidence collection, plan execution, and undo for incident response.

Collection is read-only and needs no prompt. Mutation happens only through
``execute``: the plan is validated against the most recent evidence, the host
shows the whole plan before it runs, and each step writes a manifest entry before
it touches the machine so that ``undo`` can reverse it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Any, Callable, Optional

from src.incident import model
from src.incident.evidence import indicators, sanitize, validate_snapshot

COLLECT_TIMEOUT_SECONDS = 180
OPERATION_TIMEOUT_SECONDS = 90
OPS_SCRIPT = Path(__file__).with_name("ops.ps1")
_RECORD_ID = re.compile(r"^[0-9a-f]{16}$")

Runner = Callable[[str, dict[str, Any], int], dict[str, Any]]


class OpError(RuntimeError):
    """A host operation failed, so its effect is known to be a failure."""


class OpTimeout(OpError):
    """A host operation timed out, so whether it changed anything is unknown."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def powershell_runner(op: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Run one operation in ops.ps1. The request travels on stdin, never in argv."""
    from src.discovery.discovery import _system_powershell

    powershell = _system_powershell()
    if powershell is None:
        raise OpError("Windows PowerShell was not found; incident operations run only on Windows")
    request = json.dumps({"op": op, "payload": payload}, ensure_ascii=True)
    argv = [
        str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(OPS_SCRIPT),
    ]
    try:
        completed = subprocess.run(
            argv,
            input=request,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpTimeout(f"{op} timed out; whether it changed anything is unknown") from exc
    except OSError as exc:
        raise OpError(f"{op} could not start: {exc}") from exc
    try:
        reply = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        detail = (completed.stderr or "").strip()[:200]
        raise OpError(f"{op} returned no readable result: {detail}") from exc
    if not isinstance(reply, dict) or not reply.get("ok"):
        message = reply.get("error", f"{op} failed") if isinstance(reply, dict) else f"{op} failed"
        raise OpError(str(message))
    result = reply.get("result", {})
    return result if isinstance(result, dict) else {}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _incident_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "incident"


def _evidence_path(data_dir: Path, evidence_id: str) -> Path:
    return _incident_dir(data_dir) / "evidence" / f"{evidence_id}.json"


def _manifest_path(data_dir: Path, execution_id: str) -> Path:
    return _incident_dir(data_dir) / "executions" / execution_id / "manifest.json"


def _quarantine_root(data_dir: Path) -> Path:
    return _incident_dir(data_dir) / "quarantine"


def _invalid(errors: list[str]) -> dict[str, Any]:
    return {"status": "invalid_input", "errors": errors}


def _rule_name(value: str) -> str:
    return "WDA-block-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def load_evidence(data_dir: Path, evidence_id: str) -> Optional[dict[str, Any]]:
    if not _RECORD_ID.match(evidence_id):
        return None
    return _read_json(_evidence_path(data_dir, evidence_id))


def collect(data_dir: Path, *, runner: Optional[Runner] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    """Read-only evidence collection. Writes only an evidence record under data_dir."""
    run = runner or powershell_runner
    stamp = now or _now()
    try:
        snapshot = run("collect", {}, COLLECT_TIMEOUT_SECONDS)
    except OpError as exc:
        return {"status": "unavailable", "error": str(exc)}
    problems = validate_snapshot(snapshot)
    if problems:
        return {"status": "invalid_input", "error": "collector returned an unexpected shape", "problems": problems}
    evidence_id = secrets.token_hex(8)
    record = {
        "schema": 1,
        "evidence_id": evidence_id,
        "collected_at": stamp.isoformat(),
        "snapshot": snapshot,
    }
    _write_json(_evidence_path(data_dir, evidence_id), record)
    warnings = [sanitize(w, 200) for w in snapshot["warnings"] if isinstance(w, str)]
    return {
        "status": "degraded" if warnings else "ok",
        "evidence_id": evidence_id,
        "collected_at": stamp.isoformat(),
        "counts": {
            "processes": len(snapshot["processes"]),
            "autostart_entries": len(snapshot["persistence"]),
        },
        "indicators": indicators(snapshot),
        "warnings": warnings,
        "untrusted_host_data": True,
        "note": "Indicators are leads to weigh, not proof of malware. Nothing on this machine was changed.",
    }


def execute(
    plan: Any,
    data_dir: Path,
    *,
    execute: bool,
    expected_digest: Optional[str] = None,
    runner: Optional[Runner] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Validate a plan. Changes the machine only when execute is true."""
    stamp = now or _now()
    if not isinstance(plan, dict):
        return _invalid(["plan must be an object"])
    record = load_evidence(data_dir, str(plan.get("evidence_id", "")))
    if record is None:
        return _invalid(["the plan refers to evidence that is not on record; run incident_collect first"])
    errors = model.validate_plan(plan, record, stamp)
    digest = model.plan_digest(plan)
    if expected_digest is not None and expected_digest != digest:
        errors.append("the plan changed after it was reviewed; review the new digest before approving")
    if errors:
        return {**_invalid(errors), "plan_digest": digest}
    if not execute:
        return {
            "status": "planned",
            "plan_digest": digest,
            "steps": model.render_plan(plan),
            "note": "Nothing was changed. Call again with execute true to apply exactly this plan.",
        }
    return _run_plan(plan, data_dir, digest, runner or powershell_runner, stamp)


def _operation(step: dict[str, Any], execution_id: str, index: int, data_dir: Path) -> tuple[str, dict[str, Any]]:
    action = step["action"]
    target = step["target"]
    if action == "quarantine_file":
        return "quarantine_file", {
            "path": target["path"],
            "quarantine_root": str(_quarantine_root(data_dir)),
            "exec_id": execution_id,
            "index": index + 1,
        }
    if action == "stop_process":
        return "stop_process", {
            "pid": target["pid"],
            "path": target["path"],
            "name": target["name"],
            "started": target["started"],
        }
    if action == "disable_run_value":
        return "disable_run_value", {"hive": target["hive"], "name": target["name"]}
    if action == "disable_scheduled_task":
        return "disable_scheduled_task", {"task_path": target["task_path"], "task_name": target["task_name"]}
    if action == "block_program_outbound":
        return "block_program_outbound", {"path": target["path"], "rule_name": _rule_name(target["path"])}
    return "block_remote_address", {"address": target["address"], "rule_name": _rule_name(target["address"])}


def _undo_for(step: dict[str, Any], result: dict[str, Any]) -> Optional[dict[str, Any]]:
    action = step["action"]
    target = step["target"]
    if action == "quarantine_file":
        return {"op": "undo_quarantine_file", "payload": {
            "original_path": result.get("original_path"),
            "quarantined_path": result.get("quarantined_path"),
            "sha256": result.get("sha256"),
        }}
    if action == "disable_run_value":
        return {"op": "undo_disable_run_value", "payload": {
            "hive": result.get("hive"), "name": result.get("name"), "value": result.get("value"),
        }}
    if action == "disable_scheduled_task":
        return {"op": "undo_disable_scheduled_task", "payload": {
            "task_path": target["task_path"], "task_name": target["task_name"],
        }}
    if action in {"block_program_outbound", "block_remote_address"}:
        return {"op": "undo_block_rule", "payload": {"rule_name": result.get("rule_name")}}
    return None


def _public_step(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "action", "status", "error", "undo_status")
    return {key: entry[key] for key in keys if key in entry}


def _run_plan(plan: dict[str, Any], data_dir: Path, digest: str, run: Runner, stamp: datetime) -> dict[str, Any]:
    execution_id = secrets.token_hex(8)
    path = _manifest_path(data_dir, execution_id)
    manifest: dict[str, Any] = {
        "schema": 1,
        "execution_id": execution_id,
        "plan_digest": digest,
        "evidence_id": plan["evidence_id"],
        "started_at": stamp.isoformat(),
        "status": "running",
        "steps": [{"id": s["id"], "action": s["action"], "status": "pending"} for s in plan["steps"]],
    }
    _write_json(path, manifest)
    outcome = "completed"
    for index, step in enumerate(plan["steps"]):
        entry = manifest["steps"][index]
        op, payload = _operation(step, execution_id, index, data_dir)
        entry["status"] = "running"
        _write_json(path, manifest)
        try:
            result = run(op, payload, OPERATION_TIMEOUT_SECONDS)
        except OpTimeout as exc:
            entry.update(status="unknown", error=str(exc))
            outcome = "stopped_unknown"
        except OpError as exc:
            entry.update(status="failed", error=str(exc))
            outcome = "stopped_failed"
        except Exception as exc:  # an unexpected fault leaves the effect unknown
            entry.update(status="unknown", error=f"unexpected fault: {exc}")
            outcome = "stopped_unknown"
        else:
            entry.update(status="done", result=result, undo=_undo_for(step, result))
            _write_json(path, manifest)
            continue
        _write_json(path, manifest)
        break
    manifest["status"] = outcome
    manifest["finished_at"] = _now().isoformat()
    _write_json(path, manifest)
    started = any(e["status"] in {"done", "failed", "unknown"} for e in manifest["steps"])
    return {
        "status": outcome,
        "execution_id": execution_id,
        "plan_digest": digest,
        "execution_started": started,
        "steps": [_public_step(e) for e in manifest["steps"]],
        "undo": "Call incident_undo with this execution_id to reverse the completed steps.",
    }


def undo(execution_id: Any, data_dir: Path, *, execute: bool, runner: Optional[Runner] = None) -> dict[str, Any]:
    """Reverse completed steps of one execution, newest first."""
    if not isinstance(execution_id, str) or not _RECORD_ID.match(execution_id):
        return _invalid(["execution_id is malformed"])
    path = _manifest_path(data_dir, execution_id)
    manifest = _read_json(path)
    if manifest is None:
        return _invalid(["no execution with that id"])
    if manifest.get("undone_at"):
        return _invalid(["this execution was already undone"])
    targets = [e for e in reversed(manifest["steps"]) if e.get("status") == "done" and e.get("undo")]
    if not execute:
        return {
            "status": "planned",
            "execution_id": execution_id,
            "steps": [
                {"id": e["id"], "action": e["action"], "undo": model.ACTIONS[e["action"]]["undo"]}
                for e in targets
            ],
            "note": "Nothing was changed. Call again with execute true to undo these steps.",
        }
    run = runner or powershell_runner
    failures = 0
    for entry in targets:
        try:
            run(entry["undo"]["op"], entry["undo"]["payload"], OPERATION_TIMEOUT_SECONDS)
            entry["undo_status"] = "undone"
        except Exception as exc:
            entry["undo_status"] = "undo_failed"
            entry["undo_error"] = str(exc)
            failures += 1
    manifest["undone_at"] = _now().isoformat()
    manifest["undo_status"] = "undo_partial" if failures else "undone"
    _write_json(path, manifest)
    return {
        "status": manifest["undo_status"],
        "execution_id": execution_id,
        "steps": [_public_step(e) for e in targets],
    }
