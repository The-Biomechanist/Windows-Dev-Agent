---
name: win-setup
description: Bootstrap or repair a Windows development environment when runtimes, tools, package managers, or native configuration are missing or broken.
---

# Windows Setup

## Procedure

1. **Inspect current state first.** Use `env_inspect` and project requirements. Do not reinstall tools merely because the user mentioned them.
2. **Locate the earliest missing or broken prerequisite.** Distinguish runtime absence, PATH/session propagation, version mismatch, package-manager failure, project dependency failure, and Windows feature state.
3. **Choose the native repair.** Prefer the smallest Windows-owned change that repairs that prerequisite. Do not turn a local project problem into a machine-wide setup rewrite.
4. **For system packages, resolve identity before install.** Preserve an exact ID supplied by the user or authoritative project/config state. Otherwise call `package_search`, inspect the candidates, then call `package_install` with `execute: false` for the resolved ID. Executing calls remain approval-required under the active host permission boundary.
5. **For Windows optional features**, use an explicitly approved PowerShell action only when the feature is actually required. WSL, Windows Sandbox, and Hyper-V are OS features, not ordinary package installs. Claude applies its PowerShell `PreToolUse` adapter; Codex leaves shell approval to Codex's native shell permission system while its plugin hook may still deny actions classified forbidden.
6. **For project dependencies**, switch to the project's own package manager only after the required runtime/toolchain is established. Preserve lockfiles and project conventions. Project-code/build/test execution follows the active host's ordinary permission path rather than being auto-approved merely because the launcher is familiar.
7. **Verify the repaired joint with fresh state.** Prefer the narrow version, availability, build, or project check that can establish the requested post-state. If full `env_inspect` is the right witness after a host mutation, use `force_refresh: true`; do not reuse a pre-mutation cached snapshot as proof of repair.

## Rules

- Never use `--silent` to bypass license or installer interaction.
- Resolve an exact package identity before installation; do not guess WinGet IDs.
- Do not treat a successful installer exit as proof that the current shell sees the new PATH.
- Do not mutate unrelated runtimes or package-manager state while repairing one dependency.
- If elevation or restart is required, state that post-state explicitly rather than claiming completion early.
