---
name: win-setup
description: Bootstrap or repair a Windows development environment when runtimes, tools, package managers, or native configuration are missing or broken.
---

# Windows Setup

## Procedure

1. **Inspect current state first.** Use `env_inspect` and project requirements. Preserve `null`/unknown probe state; do not reinstall a tool merely because discovery failed to establish it.
2. **Locate the earliest missing or broken prerequisite.** Distinguish runtime absence, unknown probe state, PATH/session propagation, version mismatch, package-manager failure, project dependency failure, and Windows feature state.
3. **Choose the native repair.** Prefer the smallest Windows-owned change that repairs that prerequisite. Do not turn a local project problem into a machine-wide setup rewrite.
4. **For system packages, resolve identity before install.** Preserve an exact ID supplied by the user or authoritative project/config state. Otherwise call `package_search` and resolve the exact candidate. Then call `package_install` with `execute: false`, retain its absolute `executable`, and use that exact path as `expected_executable` when later requesting `execute: true`. A `stale_plan` result requires a fresh plan; do not silently accept a newly resolved package-manager identity. `expected_executable` binds identity only—the host remains the permission authority.
5. **For Windows optional features**, use an explicitly approved PowerShell action only when the feature is actually required. WSL, Windows Sandbox, and Hyper-V are OS features, not ordinary package installs. If feature discovery is unknown because the current process could not establish it, resolve that fact before treating the feature as absent.
6. **For project dependencies**, switch to the project's own package manager only after the required runtime/toolchain is established. Preserve lockfiles and project conventions. Project-code/build/test execution follows the active host's ordinary permission path.
7. **Verify the repaired joint with fresh state.** Prefer the narrow version, availability, build, or project check that can establish the requested post-state. If full `env_inspect` is the right witness after a host mutation, use `force_refresh: true`.

## Rules

- Never use `--silent` to bypass license or installer interaction.
- Resolve an exact package identity before installation; do not guess WinGet IDs.
- Do not treat a successful installer exit as proof that the current shell sees the new PATH.
- Do not mutate unrelated runtimes or package-manager state while repairing one dependency.
- If elevation or restart is required, state that post-state explicitly rather than claiming completion early.
