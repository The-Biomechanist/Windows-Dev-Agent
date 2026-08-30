---
name: sandbox-run
description: Route command execution through WSL, a project Dev Container, or Windows Sandbox when that boundary materially fits the task. Launching execution is always host approval-gated.
---

# Sandbox Run

Choose the boundary from the task's required property, not from whichever backend happens to be installed first.

## Routing

| Requirement | `isolation_requirement` | Auto route | Boundary |
| --- | --- | --- | --- |
| Linux-native compatibility or a low-risk Linux execution surface | `linux_compatibility` | WSL | Interoperable Linux environment; **not** hostile-code containment from the Windows host. |
| Reproduce the project's declared devcontainer environment | `project_reproducibility` | Dev Container | Requires the `devcontainer` CLI and a project devcontainer configuration. |
| Run an untrusted or installer-like Windows workload away from the host | `untrusted_windows` | Windows Sandbox | Disposable Windows VM boundary with networking and clipboard disabled by this runtime. |

If the caller selects an explicit `environment`, the runtime respects that request but the reasoner still owns checking that the chosen environment satisfies the task. `environment: auto` is legal only when `isolation_requirement` is supplied; the runtime will not infer isolation semantics from backend availability.

## Procedure

1. Establish the property the boundary must provide. If it is not clear whether the need is Linux compatibility, project reproducibility, or untrusted-Windows containment, do not silently substitute one for another.
2. Inspect relevant backend availability with `env_inspect` when it is not already known.
3. Call `sandbox_run` with `execute: false`, using either an explicit environment or `environment: auto` plus the resolved `isolation_requirement`. In Codex, pass the current session/project directory as `workspace_folder`; do not use the installed plugin cache as a workspace. Planning must not launch the workload or materialize a Windows Sandbox bundle.
4. If the selected route cannot satisfy the requirement, report the missing prerequisite or deliberately choose a different explicit boundary whose tradeoff is acceptable. Never fall back to the host merely because isolation is unavailable.
5. To launch, use the active host's permission boundary. Claude forces the executing call through its host prompt; Codex configures `sandbox_run` with native MCP approval mode `prompt`. After permission is actually granted, execute with `user_approved: true` as server-side acknowledgement.
6. For WSL and Dev Container runs, evaluate the captured return code/stdout/stderr only for what they establish. For Windows Sandbox, report only that the interactive sandbox was launched; do not claim the command inside succeeded without an observation from that sandbox.
7. Executed Windows Sandbox launches return the temporary config/cleanup path. Remove that temporary bundle only after the sandbox no longer needs it.

## Safety and scope

- Sandbox execution is `approval-required` even when the command itself looks harmless.
- WSL interoperability is not a substitute for Windows Sandbox when the required property is hostile-Windows containment.
- Do not describe Hyper-V execution as supported by `sandbox_run`; Hyper-V remains an external/manual route.
- Do not map arbitrary host folders writable into Windows Sandbox. The generated runtime bundle is mapped read-only.
- Isolation does not establish semantic correctness. Verify the task result separately.
