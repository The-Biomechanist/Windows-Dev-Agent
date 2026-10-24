"""Turn a host snapshot into plain-language indicators.

Indicators are leads for a person to weigh, not verdicts. Every string came from
the machine under examination, so it is untrusted: it is length-capped, control
characters are removed, and nothing in it is treated as an instruction.
"""

from __future__ import annotations

from typing import Any

from src.incident.model import USER_WRITABLE_MARKERS

SEVERITY_RANK = {"high": 0, "notice": 1}
AUTOSTART_KINDS = frozenset({"run_key", "scheduled_task", "startup_item"})


def validate_snapshot(snapshot: Any) -> list[str]:
    if not isinstance(snapshot, dict):
        return ["snapshot is not an object"]
    problems = []
    for key in ("processes", "persistence", "warnings"):
        if not isinstance(snapshot.get(key), list):
            problems.append(f"{key} must be a list")
    for key in ("defender", "proxy", "hosts"):
        if not isinstance(snapshot.get(key), dict):
            problems.append(f"{key} must be an object")
    return problems


def sanitize(value: Any, limit: int = 160) -> str:
    text = "".join(ch if ch.isprintable() else "?" for ch in str(value))
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _user_writable(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in USER_WRITABLE_MARKERS)


def _indicator(identifier: str, severity: str, title: str, explanation: str, ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": identifier,
        "severity": severity,
        "title": title,
        "explanation": explanation + " This is a lead to check, not proof of malware.",
        "evidence": ref,
    }


def indicators(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for proc in snapshot["processes"]:
        if not isinstance(proc, dict):
            continue
        path = proc.get("path")
        connections = proc.get("connections") if isinstance(proc.get("connections"), int) else 0
        ref = {"kind": "process", "pid": proc.get("pid"), "name": sanitize(proc.get("name", ""), 80)}
        if not isinstance(path, str) or not path:
            if connections > 0:
                found.append(_indicator(
                    "unverifiable_network_process", "notice", "Network activity from a program Windows would not identify",
                    f"{sanitize(proc.get('name', ''), 80)} has {connections} network connection(s), but its location could not be read. It may be a protected process.",
                    ref,
                ))
            continue
        ref["path"] = sanitize(path)
        if proc.get("signed") is False and _user_writable(path):
            severity = "high" if connections > 0 else "notice"
            detail = " It also has network connections." if connections > 0 else ""
            found.append(_indicator(
                "unsigned_program_in_user_folder", severity, "Unsigned program running from a user folder",
                f"{sanitize(path)} is running, is not signed by a trusted publisher, and sits in a folder ordinary software and malware both use.{detail}",
                ref,
            ))
    for entry in snapshot["persistence"]:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path or entry.get("signed") is not False or not _user_writable(path):
            continue
        kind = entry.get("kind")
        ref = {"kind": kind, "name": sanitize(entry.get("name", ""), 80), "path": sanitize(path)}
        if kind in AUTOSTART_KINDS:
            found.append(_indicator(
                "unsigned_autostart_from_user_folder", "high", "Unsigned program set to start automatically from a user folder",
                f"{sanitize(path)} is unsigned, sits in a user folder, and is started by {kind.replace('_', ' ')} entry '{sanitize(entry.get('name', ''), 80)}'.",
                ref,
            ))
        else:
            found.append(_indicator(
                "unsigned_service_from_user_folder", "notice", "Unsigned service binary in a user folder",
                f"A service runs {sanitize(path)}, which is unsigned and sits in a user folder. Services are reported, not changed, by this plugin.",
                ref,
            ))
    defender = snapshot["defender"]
    if defender.get("realtime_protection") is False:
        found.append(_indicator(
            "defender_realtime_off", "high", "Windows Defender real-time protection is off",
            "Real-time protection is disabled. That can be a deliberate policy setting, or a sign that something turned it off.",
            {"kind": "defender"},
        ))
    threats = defender.get("recent_threats")
    if isinstance(threats, list) and threats:
        names = ", ".join(sanitize(t.get("name", ""), 60) for t in threats if isinstance(t, dict))
        found.append(_indicator(
            "recent_defender_detections", "high", "Windows Defender has recorded recent detections",
            f"Defender reported: {names or 'threat detections'}. Run a full scan and read the Defender history before changing anything.",
            {"kind": "defender"},
        ))
    proxy = snapshot["proxy"]
    if proxy.get("enabled") is True:
        found.append(_indicator(
            "system_proxy_enabled", "notice", "Internet traffic goes through a proxy",
            f"A system proxy is set to {sanitize(proxy.get('server') or 'an unknown server', 120)}. Confirm you set it; malware often does.",
            {"kind": "proxy"},
        ))
    hosts = snapshot["hosts"]
    count = hosts.get("non_default_entries")
    if isinstance(count, int) and count > 0:
        sample = "; ".join(sanitize(line, 120) for line in hosts.get("sample", [])[:5] if isinstance(line, str))
        found.append(_indicator(
            "hosts_file_has_entries", "notice", "The hosts file redirects names",
            f"The hosts file has {count} non-default entr{'y' if count == 1 else 'ies'}. Examples: {sample}",
            {"kind": "hosts"},
        ))
    found.sort(key=lambda item: SEVERITY_RANK.get(item["severity"], 9))
    return found
