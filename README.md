# Windows Dev Agent

A Windows-native development orchestration plugin built around PowerShell-first discovery, capability routing, explicit execution planning, safety-gated mutation, and isolated fallback environments.

It combines a Claude Code plugin surface with a Python MCP stdio backend. The goal is not to make Windows behave like Unix: it prefers Windows-native control planes first, then routes to WSL, Windows Sandbox, Hyper-V, or dev-container isolation when the task actually calls for them.

## Install

Claude Code plugin package:

```text
/plugin install windows-dev-agent
```

APM package:

```text
apm install windows-dev-agent
```

The plugin manifest declares version **0.2.0** and requires Claude Code **1.0.33+**.

## Primary commands

- `/windows-dev-agent:env` — inspect the Windows development environment: OS, runtimes, package managers, WSL, Dev Drive, editors, and related tooling.
- `/windows-dev-agent:plan <task>` — produce a structured execution plan with entry criteria, ordered steps, exit criteria, rollback information, and safety classification before mutation.
- `/windows-dev-agent:defrag` — inspect existing agent infrastructure such as extensions, MCP servers, and agent configuration, then decide what should be absorbed, routed, retained, or removed.

## MCP tool surface

The Python MCP server exposes focused tools for orchestration rather than one unrestricted shell interface:

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Build a full Windows environment snapshot. |
| `tool_discover` | Discover runtimes, editors, package managers, and version-control tools. |
| `capability_run` | Route a named capability through the best available configured tool. |
| `workflow_plan` | Represent a task as a structured plan before execution. |
| `package_install` | Prepare or perform package installation through WinGet, Chocolatey, or Scoop with approval semantics. |
| `sandbox_run` | Route commands into WSL, Windows Sandbox, or a dev container when isolation is appropriate. |
| `logs_query` | Inspect recent audit/log events. |
| `mcp_audit` | Inspect configured MCP servers and surface overlap or compatibility concerns. |

The MCP server is configured through `.mcp.json` and runs as:

```text
python -m src.mcp.server
```

## Architecture

### Plugin layer

Read directly by the agent host:

- `commands/` — explicit slash-command workflows;
- `skills/` — auto-triggered task guidance;
- `agents/` — orchestration behavior;
- `hooks/` — pre/post-tool policy and safety gates;
- `.claude-plugin/plugin.json` — Claude Code plugin metadata;
- `apm.yml` — APM package metadata.

### Python orchestration layer

The `src/` tree contains the runtime machinery for:

- environment and tool discovery;
- capability routing;
- workflow representation;
- execution and sandbox handling;
- safety classification;
- MCP transport;
- logging, observability, and audit support.

`capabilities.yaml` provides the capability-to-tool routing surface used by the backend.

## Routing preference

The project favors native Windows tooling before compatibility layers:

1. PowerShell and Windows-native commands;
2. WinGet and native package-management paths;
3. WMI/CIM and Windows management surfaces;
4. MSBuild / .NET tooling;
5. WSL when the workload is genuinely Linux-oriented or it is the best available isolation route;
6. Windows Sandbox, Hyper-V, or dev containers for isolation-sensitive execution.

The point is not to forbid alternatives—it is to keep the control plane appropriate to the host and task.

## Safety model

| Class | Default behavior |
| --- | --- |
| `read-only` | May run autonomously. |
| `reversible` | May run with auditability and a recovery path. |
| `approval-required` | Show the proposed action and require confirmation before mutation. |
| `checkpoint` | Explain the elevated risk and require explicit confirmation. |
| `forbidden` | Do not execute without a direct human instruction that changes the boundary. |

Package installation and sandbox execution are exposed with dry-run / approval-oriented behavior rather than silently mutating the machine.

## Requirements

- Windows 10 or Windows 11;
- PowerShell 5.1+;
- Python 3.9+;
- `PyYAML` for capability configuration;
- Claude Code 1.0.33+ when using the Claude Code plugin surface.

## Repository map

- `.claude-plugin/` — plugin manifest.
- `.mcp.json` — MCP server configuration.
- `commands/` — `env`, `plan`, and `defrag` command definitions.
- `skills/` — task-specific skill guidance.
- `agents/` — orchestration agent definitions.
- `hooks/` — tool safety hooks.
- `src/` — Python orchestration and MCP backend.
- `tests/` — backend tests.
- `capabilities.yaml` — named capability routing configuration.
- `docs/` — design and architecture material.

The repository is also marked as a GitHub template, so it can serve as a starting point for a Windows-native agent setup rather than only as a package to install in place.
