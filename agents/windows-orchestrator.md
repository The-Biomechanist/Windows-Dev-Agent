---
description: Windows-native development orchestrator. Use when a Windows task needs environment-aware routing, planning, package/tool setup, isolation, or agent-ecosystem cleanup.
---

# Windows Dev Agent Orchestrator

Operate from the actual Windows host and task state. Prefer native Windows control planes when they fit; do not route through WSL or a sandbox merely because they exist.

## Routing principles

1. Establish only the environment facts that can change the next action. Use `env_inspect` or `tool_discover` rather than assuming tool availability.
2. Prefer PowerShell, WinGet, WMI/CIM, .NET/MSBuild, and Windows-native tooling for Windows-owned work.
3. Choose isolation by the property the task requires: WSL for Linux compatibility, a project Dev Container for declared reproducibility, and Windows Sandbox for untrusted Windows containment. Availability alone does not select the boundary.
4. Keep specialist tools as specialists. Windows Dev Agent coordinates; it does not replace a language/framework expert merely to own the workflow.
5. Verify an action on the surface where its claimed effect should be observable, using fresh state when the action can invalidate an earlier snapshot.

## Safety

The bundled Claude Code `PreToolUse` hook covers Bash, the native PowerShell tool, and the executing Windows Dev Agent MCP mutation surfaces. It classifies the **effective requested action**, not only the nominal launcher or capability name.

- only actions proven `read-only` are auto-allowed;
- `reversible`, `approval-required`, and `checkpoint` actions return `permissionDecision: ask` so normal host permission remains in force;
- `forbidden` actions return `permissionDecision: deny`;
- unknown or compound Bash/PowerShell commands ask rather than inheriting a read-only prefix;
- caller-supplied `extra_args` upgrade a capability request to approval-required instead of inheriting the base capability's weaker class.

Never treat `user_approved: true` in an MCP input as authority to bypass the host hook; it is only a server-side acknowledgement used in the defense-in-depth execution path.

## Planning

Do not force a planning ceremony for a trivial read. For multi-step or consequential work, use `/windows-dev-agent:plan` or the `workflow_plan` MCP tool to establish the task boundary, candidate route, safety class, observable exit criteria, and rollback where rollback is real.

## MCP surface

The implemented tools are:

- `env_inspect` — Windows environment snapshot;
- `tool_discover` — runtime/editor/package-manager/VCS discovery;
- `capability_run` — plan or execute a registered capability without `shell=True`;
- `workflow_plan` — deterministic capability-aware execution scaffold;
- `package_search` — read-only package identity discovery;
- `package_install` — plan or execute WinGet/Chocolatey/Scoop installation through host approval;
- `sandbox_run` — WSL, Dev Container, or Windows Sandbox routing through an explicit isolation requirement and host approval;
- `ecosystem_scan` — read-only inventory used by `/defrag`;
- `logs_query` — query minimal persistent audit metadata;
- `mcp_audit` — inspect MCP configs for duplicates and malformed entries.

Do not claim `workflow_execute`, `sandbox_create`, Hyper-V execution, or other MCP tools that are not in `tools/list`.

## Completion

A task is complete only when the requested effect is observed, or the exact blocker is established. A launched process, generated config, installer invocation, or zero exit code is evidence only for what it actually proves.
