---
description: Bootstrap or repair a Windows development environment when runtimes, tools, package managers, or native configuration are missing or broken.
---

# Windows Setup

## Procedure

1. **Inspect current state first.** Use `env_inspect` and project requirements. Do not reinstall tools merely because the user mentioned them.
2. **Locate the earliest missing or broken prerequisite.** Distinguish runtime absence, PATH/session propagation, version mismatch, package-manager failure, project dependency failure, and Windows feature state.
3. **Choose the native repair.** Prefer the smallest Windows-owned change that repairs that prerequisite. Do not turn a local project problem into a machine-wide setup rewrite.
4. **For system package installation, use `package_install`.** Call it with `execute: false` first to expose the exact source and argv. Executing calls are approval-required and are independently forced through the Claude Code host prompt.
5. **For Windows optional features**, use an explicitly approved PowerShell action only when the feature is actually required. WSL, Windows Sandbox, and Hyper-V are OS features, not ordinary package installs.
6. **For project dependencies**, switch to the project's own package manager only after the required runtime/toolchain is established. Preserve lockfiles and project conventions.
7. **Verify the repaired joint.** Re-run the narrow version, availability, build, or project check that can establish the requested state. Use `env_inspect` again only when a full environment refresh is relevant.

## Rules

- Never use `--silent` to bypass license or installer interaction.
- Resolve an exact package identity before installation; do not guess WinGet IDs.
- Do not treat a successful installer exit as proof that the current shell sees the new PATH.
- Do not mutate unrelated runtimes or package-manager state while repairing one dependency.
- If elevation or restart is required, state that post-state explicitly rather than claiming completion early.
