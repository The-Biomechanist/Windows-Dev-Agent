---
name: sandbox-run
description: Route command execution through WSL, a project Dev Container, or Windows Sandbox when that boundary materially fits the task. Launching execution is host approval-gated.
---

# Sandbox Run

Choose the boundary from the task's required property, not from whichever backend happens to be installed first.

## Routing

| Requirement | `isolation_requirement` | Auto route | Boundary |
| --- | --- | --- | --- |
| Linux-native compatibility or a low-risk Linux execution surface | `linux_compatibility` | WSL | Interoperable Linux environment; **not** hostile-code containment from the Windows host. |
| Reproduce the project's declared devcontainer environment | `project_reproducibility` | Dev Container | Requires the `devcontainer` CLI and project devcontainer configuration. |
| Run an untrusted Windows artifact away from the host | `untrusted_windows` | Windows Sandbox | Disposable Windows VM with networking and clipboard disabled; the selected workload must be staged explicitly. |

`environment: auto` is legal only when `isolation_requirement` is supplied. For `untrusted_windows`, also supply one or more workspace-relative `payload_paths`; the runtime refuses an auto-routed hostile-workload plan that would launch a sandbox without the workload inside it.

## Procedure

1. Establish the property the boundary must provide. Do not silently substitute Linux compatibility for hostile-Windows containment.
2. Inspect relevant backend availability with `env_inspect` when it is not already established. Treat `null`/unknown availability as unresolved rather than missing.
3. For WSL or Dev Container work, use the active project as `workspace_folder`. WSL execution enters that Windows project directory through WSL's `--cd` boundary and uses `sh -lc`; a task requiring Bash-specific semantics should request Bash explicitly.
4. For Windows Sandbox, identify exactly which project files/directories the isolated command needs and pass them as workspace-relative `payload_paths`. The runtime validates that they stay inside the workspace, rejects symbolic-link escapes and overlapping selections, and limits the staged input to 10,000 filesystem entries and 1 GiB total file bytes. Write the inner command relative to the staged payload root (`C:\WDAShare\payload`).
5. Call `sandbox_run` with `execute: false` and inspect the selected route, payload list, and launch plan. Planning must not create the temporary bundle or launch the workload. In Codex, a trusted `PermissionRequest` hook may auto-allow this plan-only request; without trusted hooks, the host may prompt for the plan.
6. To launch, call the same reviewed tool with `execute: true`. The active host decides whether that exact call proceeds; do not invent a second approval token.
7. For WSL and Dev Container runs, use captured return code/stdout/stderr only for what they establish. For Windows Sandbox, report only that the interactive sandbox launched; the inner command remains `unknown` until an observation from inside the sandbox establishes its outcome.
8. An executed Windows Sandbox launch returns `cleanup_path`. Remove the temporary bundle only after the sandbox no longer needs it. If staging or process launch fails before the Sandbox starts, the runtime removes the partial bundle itself.

## Safety and scope

- Sandbox execution is `approval-required` even when the inner command looks harmless.
- WSL interoperability is not a substitute for Windows Sandbox containment.
- Hyper-V is not an implemented `sandbox_run` backend.
- Do not map arbitrary host folders writable into Windows Sandbox. Only the generated staging bundle is mapped, read-only.
- Isolation establishes a boundary, not semantic correctness. Verify the task result separately.
