# Windows Dev Agent

A Windows-native Claude Code plugin for environment discovery, explicit workflow planning, safety-aware capability routing, package/tool setup, isolation, and agent-ecosystem cleanup.

The control plane is Windows-native where Windows owns the problem: PowerShell, WinGet, WMI/CIM, .NET/MSBuild, and Windows management surfaces come first. WSL, Dev Containers, and Windows Sandbox are selected only when their actual execution boundary fits the task.

## Run locally

From a checkout of this repository:

```text
claude --plugin-dir .
```

The runtime uses only the Python standard library. No `pip install` step is required for the plugin or its MCP server.

If you publish or install it through a configured Claude Code marketplace or APM registry, the package name is `windows-dev-agent`.

## Commands

- `/windows-dev-agent:env` — inspect the Windows development environment.
- `/windows-dev-agent:plan <task>` — build a structured execution plan before consequential work.
- `/windows-dev-agent:defrag` — inventory existing agent infrastructure, identify concrete overlap, and plan a reversible consolidation.

## Runtime state boundaries

The bundled `.mcp.json` keeps three identities separate:

- `${CLAUDE_PLUGIN_ROOT}` — plugin code/config and the MCP server working directory;
- `${CLAUDE_PLUGIN_DATA}` — persistent discovery cache and minimal audit metadata;
- `${CLAUDE_PROJECT_DIR}` — the user's project and the default working directory for project-scoped capabilities, sandbox workspace, ecosystem scanning, and MCP auditing.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Native Windows environment snapshot with explicit `force_refresh` for stale-state-sensitive verification. |
| `tool_discover` | Focused runtime/editor/package-manager/VCS discovery. |
| `capability_run` | Plan or execute a named capability from `capabilities.yaml` using argv vectors and `shell=False`. |
| `workflow_plan` | Build a deterministic capability-aware execution scaffold. |
| `package_search` | Search an installed Windows package manager to resolve package identity before mutation. |
| `package_install` | Plan or execute an exact WinGet, Chocolatey, or Scoop install through the host approval boundary. |
| `sandbox_run` | Route through WSL, a project Dev Container, or Windows Sandbox using the task's explicit isolation requirement. |
| `ecosystem_scan` | Read-only inventory for `/defrag`: VS Code extensions, MCP configs, agent configs, Claude plugin directories, and optional WinGet inventory. |
| `logs_query` | Query minimal persistent Windows Dev Agent audit metadata across recorded sessions. |
| `mcp_audit` | Inspect project/user MCP configs for configured servers, duplicate names, and malformed entries without exposing environment values. |

## Safety model

The plugin safety hook is **tightening-only** over Claude Code's native permission system. It never returns `permissionDecision: allow`, so it never grants execution that Claude Code would otherwise have prompted for or denied.

The `PreToolUse` hook covers Bash, Claude Code's native PowerShell tool, and executing Windows Dev Agent MCP mutation calls. Claude Code scopes tools from plugin-bundled MCP servers internally; the hook supports both that installed-plugin identity and the bare server identity used by direct/local execution.

| Effective class | Plugin decision |
| --- | --- |
| `read-only` | no plugin decision — normal Claude Code permission flow remains authoritative |
| `reversible` | no plugin decision — normal Claude Code permission flow remains authoritative |
| `approval-required` | `ask` — force explicit host confirmation |
| `checkpoint` | `ask` — force explicit host confirmation |
| `forbidden` | `deny` |

Additional guards:

- unknown Bash or PowerShell commands ask rather than defaulting to a weaker classification;
- compound, redirected, substituted, or dynamically invoked shell commands cannot inherit a read-only prefix classification;
- caller-supplied `extra_args` cannot inherit a base capability's weaker safety class and instead require approval;
- package installation and sandbox launch are plan-first;
- the MCP server separately blocks forbidden capabilities and requires an acknowledgement before reversible or approval-required direct capability execution.

The host permission system remains the human-confirmation authority. A model-provided `user_approved: true` value is not proof of permission and cannot bypass the plugin hook.

## Capability routing

`capabilities.yaml` is deliberately small and executable. It uses the JSON-compatible subset of YAML so the installed runtime can parse it with Python's standard `json` module.

The current catalog covers:

- Git status inspection;
- Python and JavaScript/TypeScript linting;
- Python and .NET tests;
- .NET builds;
- GitHub PR creation as an approval-required publication action.

Configured commands are argv arrays. Base safety describes the configured request; if a caller appends additional arguments, the effective request is upgraded to approval-required instead of assuming the base classification still applies.

Child processes launched by the MCP runtime, capability runner, and environment discovery receive `DEVNULL` stdin rather than inheriting the MCP server's stdio transport. An unexpectedly interactive child therefore cannot consume future JSON-RPC requests as prompt input.

## Package identity and freshness

A package mutation should not begin from a guessed ID. Use an exact ID supplied by the user or authoritative project/config state, or call `package_search` and resolve the matching candidate before `package_install`.

WinGet search is noninteractive and does not auto-accept source agreements. If a source agreement prevents read-only discovery, that unresolved prerequisite is surfaced instead of being silently accepted by the search operation.

Successful package installation invalidates the cached environment snapshot. More generally, when a host mutation can change the state a later step consumes, verify with the narrowest fresh probe or call `env_inspect(force_refresh=true)` rather than treating a pre-mutation snapshot as current evidence.

## Isolation

`environment: auto` does **not** mean "first backend installed." It requires `isolation_requirement` and routes by the property the task needs:

- `linux_compatibility` → WSL;
- `project_reproducibility` → a configured project Dev Container;
- `untrusted_windows` → Windows Sandbox.

WSL is an interoperable Linux environment and is not treated as hostile-Windows containment. Windows Sandbox plans do not materialize a bundle. On approved execution, the runtime creates a temporary `.wsb` bundle with networking and clipboard disabled, maps only that generated bundle read-only, and launches the sandbox interactively. A successful launch is not reported as proof that the command inside succeeded.

Hyper-V is not claimed as an implemented `sandbox_run` backend.

## Audit trail

The plugin persists only metadata its audit surfaces consume. It does **not** retain arbitrary command bodies, tool inputs, stdout/stderr, or unrelated tool responses.

- `PreToolUse` records safety classification and whether the plugin forced `ask`/`deny` or deferred to the host, with session/tool identity.
- `PostToolUse` and `PostToolUseFailure` are scoped to Windows Dev Agent MCP operations and record minimal completion metadata.
- the `Stop` hook reads its current `session_id` and reports only events from that session;
- `logs_query` is explicitly a persistent-history surface and labels itself accordingly.

State lives under `${CLAUDE_PLUGIN_DATA}/agent.log`. This is a local structured audit log; the project does not claim external telemetry export.

## Architecture

The release path is intentionally small:

```text
Claude Code plugin layer
  commands/  skills/  agents/  hooks/
                 │
                 ▼
          .mcp.json / stdio
                 │
                 ▼
          src/mcp/server.py
          ├─ src/capabilities.py
          ├─ src/discovery/
          ├─ src/safety/
          ├─ src/observability/
          └─ src/models/environment.py
```

Disconnected execution, graph, workflow, and schema scaffolding is not kept merely because it once had tests.

## Requirements

- Windows 10 or Windows 11 for full native behavior;
- PowerShell 5.1+;
- Python 3.9+;
- a current Claude Code release supporting plugin MCP servers, the PowerShell tool, and structured `PreToolUse` permission decisions.

Some read-only/runtime tests are portable, but release verification runs on Windows.

## Verification

GitHub Actions on `windows-latest` checks:

1. runtime compilation;
2. the pytest regression suite;
3. MCP initialization and the exact expected tool surface.

The regression suite is intended to discriminate the public contracts above: installed-plugin MCP naming, PowerShell/Bash permission routing, argument-dependent authority, package identity flow, cache freshness, isolation selection, audit ownership/session binding, transport isolation, and plan-versus-execute boundaries.

For local development:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src
python -m pytest tests -q
```

A green run supports only the behavior exercised by those tests; it is not evidence for unexecuted interactive host or sandbox outcomes.
