# Windows Dev Agent

A Windows-native Claude Code plugin for environment discovery, explicit workflow planning, safe capability routing, package/tool setup, isolation, and agent-ecosystem cleanup.

The project deliberately keeps the control plane Windows-native: PowerShell, WinGet, WMI/CIM, .NET/MSBuild, and Windows management surfaces come first. WSL, Dev Containers, and Windows Sandbox are used when the workload or isolation boundary actually calls for them.

## Run locally

From a checkout of this repository:

```text
claude --plugin-dir .
```

The runtime uses only the Python standard library. No `pip install` step is required for the plugin or its MCP server. The plugin manifest declares version **0.2.0** and uses Claude Code's current plugin manifest schema.

If you publish or install it through a configured Claude Code marketplace or APM registry, the package name is `windows-dev-agent`.

## Commands

- `/windows-dev-agent:env` — inspect the Windows development environment.
- `/windows-dev-agent:plan <task>` — build a structured execution plan before mutation.
- `/windows-dev-agent:defrag` — inventory existing agent infrastructure, identify real capability overlap, and plan a reversible consolidation.

## MCP tools

The bundled `.mcp.json` gives the server three distinct locations instead of conflating them:

- `${CLAUDE_PLUGIN_ROOT}` — immutable-ish plugin code/config and the MCP server working directory;
- `${CLAUDE_PLUGIN_DATA}` — persistent discovery cache and audit state that survives plugin updates;
- `${CLAUDE_PROJECT_DIR}` — the user's project and the default working directory for project-scoped capabilities, sandbox workspace, ecosystem scanning, and MCP auditing.

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Native Windows environment snapshot with a degraded fallback when full discovery is unavailable. |
| `tool_discover` | Discover common runtimes, editors, package managers, and VCS tools. |
| `capability_run` | Plan or execute a named capability from `capabilities.yaml`. Configured commands are argv vectors, run with `shell=False`, and default to the Claude project directory. |
| `workflow_plan` | Build a deterministic execution scaffold and rank relevant registered capabilities for a task. |
| `package_install` | Plan or execute a WinGet, Chocolatey, or Scoop install through the approval gate. |
| `sandbox_run` | Plan or run isolated commands through WSL, a Dev Container, or Windows Sandbox when available. |
| `ecosystem_scan` | Read-only project/user inventory for `/defrag`: VS Code extensions, MCP configs, agent configs, Claude plugin directories, and optional WinGet inventory. |
| `logs_query` | Query the redacted structured audit log in persistent plugin data. |
| `mcp_audit` | Inspect project/user MCP configs for configured servers, duplicate names, and malformed entries without exposing environment values. |

## Safety model

Safety decisions are enforced at the **Claude Code host permission surface**, not by trusting the model to claim that approval happened.

The bundled `PreToolUse` hook reads Claude Code's JSON hook event on stdin and returns a structured decision:

| Class | Host decision |
| --- | --- |
| `read-only` | `allow` |
| `reversible` | `allow` |
| `approval-required` | `ask` — Claude Code prompts the user |
| `checkpoint` | `ask` — Claude Code prompts the user |
| `forbidden` | `deny` |

Unknown Bash commands default to **ask**, not allow. Package installation and sandbox launch are plan-first: `execute: false` returns the intended action without executing it; executing calls are forced through the host prompt. Approval-required capabilities such as PR publication are classified from the same capability catalog the MCP server uses.

The MCP server also refuses forbidden capabilities and requires the executing request to acknowledge the approval boundary. That is defense in depth; the Claude Code hook is the human-confirmation authority when the server is used through this plugin.

## Capability routing

`capabilities.yaml` is intentionally small and executable rather than a catalog of aspirational stubs. To keep plugin startup self-contained, it uses the **JSON-compatible subset of YAML** and is parsed with Python's standard `json` module—no third-party YAML package is required.

It currently covers:

- Git status inspection;
- Python and JavaScript/TypeScript linting;
- Python and .NET tests;
- .NET builds;
- GitHub PR creation as an approval-required publication action.

Tool commands are stored as argument arrays. Runtime execution appends extra arguments as separate argv entries and never interpolates them into a host-shell command.

## Isolation

`sandbox_run` supports three concrete routes:

- **WSL** — captured execution through `wsl -- bash -lc ...`;
- **Dev Container** — captured execution through the `devcontainer` CLI;
- **Windows Sandbox** — on approved execution, generates a temporary `.wsb` bundle with networking and clipboard disabled, maps only the generated launch bundle read-only, and opens the sandbox interactively.

A plan-only Windows Sandbox call does not materialize the bundle. Windows Sandbox launch is not treated as proof that the command inside succeeded. Hyper-V is not claimed as an implemented `sandbox_run` backend.

## Audit trail

Plugin hooks write a structured JSONL audit trail to `${CLAUDE_PLUGIN_DATA}/agent.log`, alongside the persistent environment-discovery cache:

- PreToolUse safety class and permission decision;
- successful tool completions;
- failed tool completions;
- redaction of keys that look like tokens, passwords, secrets, credentials, cookies, or authorization values.

The Stop hook reads the same persistent log and prints a concise session summary. This is a local structured audit log; the project does **not** claim external OpenTelemetry export.

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
          ├─ src/execution/
          ├─ src/safety/
          ├─ src/observability/
          └─ src/models/
```

Earlier unused graph/workflow/schema scaffolding and tracked cache material were removed rather than preserved as architecture theater. Machine-local Claude permission settings are also excluded from the distribution.

## Requirements

- Windows 10 or Windows 11 for full native behavior;
- PowerShell 5.1+;
- Python 3.9+;
- a current Claude Code release that supports plugin MCP servers and structured `PreToolUse` permission decisions.

Some read-only/runtime tests are portable, but release verification runs on Windows.

## Verification

GitHub Actions runs on `windows-latest` and checks:

1. runtime compilation with `compileall`;
2. the complete pytest suite;
3. MCP initialization and the expected tool surface.

For local development:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src
python -m pytest tests -q
```

A release claim should follow the observed CI result, not a commit message saying the project is complete.
