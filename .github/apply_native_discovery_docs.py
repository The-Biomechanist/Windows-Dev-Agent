from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "README.md",
    '''Native discovery is performed with Windows-owned PowerShell and returns one canonical `EnvironmentSnapshot` shape even when the probe degrades or fails unexpectedly.\n\nThe cache uses the same canonical snapshot representation, is capped at 1 MiB, written atomically, and expires after five minutes.''',
    '''Native discovery is performed with Windows-owned PowerShell and returns one canonical `EnvironmentSnapshot` shape even when the probe degrades or fails unexpectedly. WSL discovery treats the legacy inbox optional component as only one possible implementation: Store WSL, native service registration, machine WSL policy, and the current user's registered distributions are evaluated separately. `wsl_installed` records control-plane installation, while `wsl_available` requires policy permission plus a registered default distribution that WDA can actually target for `linux_compatibility`. Dev Drive inventory is established from the filesystem's `PERSISTENT_VOLUME_STATE_DEV_VOLUME` flag through `FSCTL_QUERY_PERSISTENT_VOLUME_STATE`; a user-chosen volume label is not treated as Dev Drive identity.\n\nThe cache uses the same canonical snapshot representation, is capped at 1 MiB, written atomically, and expires after five minutes.''',
)
replace_once(
    "README.md",
    '''`environment:auto` chooses the backend dictated by that property. If the caller names an explicit backend that does not satisfy the requirement, WDA rejects the request instead of silently weakening the boundary. `sandbox_run(execute:false)` also returns the absolute backend `executable` plus its identity kind and SHA-256 fingerprint; execution must echo all three through the matching `expected_*` fields so a changed backend identity invalidates the plan before staging or launch.''',
    '''`environment:auto` chooses the backend dictated by that property. If the caller names an explicit backend that does not satisfy the requirement, WDA rejects the request instead of silently weakening the boundary. The WSL route requires the Windows-owned `wsl.exe`, machine policy that permits WSL, and a valid registered default distribution under the current user's native WSL registration; `wsl.exe` presence alone is not treated as Linux execution availability. `sandbox_run(execute:false)` also returns the absolute backend `executable` plus its identity kind and SHA-256 fingerprint; execution must echo all three through the matching `expected_*` fields so a changed backend identity invalidates the plan before staging or launch.''',
)
replace_once(
    "CHANGELOG.md",
    '''- Resolve runtime-owned Windows control-plane binaries such as discovery PowerShell, WSL, and Windows Sandbox from trusted Windows locations rather than PATH; Claude also binds its bootstrap PowerShell to the Windows system installation.\n''',
    '''- Resolve runtime-owned Windows control-plane binaries such as discovery PowerShell, WSL, and Windows Sandbox from trusted Windows locations rather than PATH; Claude also binds its bootstrap PowerShell to the Windows system installation.\n- Make WSL discovery Store/inbox agnostic: distinguish control-plane installation from usable routing, honor machine WSL/WSL1 policy, read the current user's native WSL distribution registration, and require a valid default distribution before advertising `linux_compatibility`.\n''',
)
replace_once(
    "CHANGELOG.md",
    '''- Revalidate Windows Sandbox payload paths, entry/byte budgets, and opened file/directory identities while staging so a post-validation junction/symlink swap cannot redirect WDA-owned copies.\n''',
    '''- Revalidate Windows Sandbox payload paths, entry/byte budgets, and opened file/directory identities while staging so a post-validation junction/symlink swap cannot redirect WDA-owned copies.\n- Replace Dev Drive volume-label inference with the native `FSCTL_QUERY_PERSISTENT_VOLUME_STATE` / `PERSISTENT_VOLUME_STATE_DEV_VOLUME` filesystem identity and preserve whether the inventory was actually established.\n''',
)

print("native discovery documentation updated")
