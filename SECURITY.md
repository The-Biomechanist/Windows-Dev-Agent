# Security Policy

Windows Dev Agent executes local development operations on Windows through Claude Code or Codex. Treat the host permission system, project boundary, executable identity, and external observation as separate security surfaces.

## Supported versions

| Version | Status |
| --- | --- |
| 0.5.x | Supported after 0.5.0 is published. |
| 0.4.3 | Supported until 0.5.0 is published. |
| 0.4.2 and older | Not supported. |

Security fixes normally target the latest supported release line. A vulnerability that affects the release/install mechanism itself may require a new immutable payload rather than an in-place repair.

## Reporting a vulnerability

Prefer GitHub's private vulnerability-reporting / Security Advisory surface for this repository when the **Report a vulnerability** option is available.

If private vulnerability reporting is not available, open a minimal repository issue asking for a private reporting channel. Do **not** put exploit details, credentials, local paths, private project contents, or a working proof of concept in a public issue.

A useful private report includes:

- affected WDA version and commit SHA;
- host (Claude Code or Codex) and host version;
- Windows version/build and Python version;
- the security boundary that was crossed;
- the smallest reproducible sequence;
- whether external execution actually started and what post-state was observed;
- whether the issue requires a malicious project, malicious PATH entry, symlink/reparse point, crafted MCP call, or untrusted Sandbox payload.

## Security boundary

### Host permission is authoritative

WDA does not create its own model-controlled approval token. Claude Code or Codex decides whether an execution-capable call is permitted. WDA hooks can tighten or, in narrowly proven Codex plan-only cases, remove a redundant prompt; they do not make an executing mutation intrinsically safe.

A host UI bug, a user-approved destructive request, or a host policy that deliberately allows a command is outside WDA's promise that its own runtime will manufacture an additional approval ceremony.

### Request validation is still enforced in WDA

The MCP runtime validates tool arguments again at execution time. Advertised JSON schemas are not trusted as the only enforcement layer. Unknown fields, wrong primitive types, oversized argument collections, incompatible Sandbox routing, and other invalid direct calls are rejected by the runtime.

### Executable identity matters

WDA resolves external executables to absolute identities and captured subprocess execution refuses an unresolved/bare executable. For ordinary Windows tool lookup, WDA does not use Python's current-directory-sensitive `shutil.which()` semantics: it enumerates absolute inherited `PATH` entries itself, excludes the process current directory plus empty/relative PATH entries, and rejects relative command paths. This prevents the active project or plugin working directory from becoming implicit executable authority merely because Windows/Python would search it first.

For plan-first `capability_run`, `package_install`, and `sandbox_run`, the `execute:false` result exposes the absolute `executable` compatibility field plus `executable_identity_kind` and `executable_identity_sha256`; together they identify the reviewed command target. A later `execute:true` call must echo those values through the matching `expected_*` fields. Native executable files are fingerprinted from the exact opened file, Windows App Execution Aliases from their AppExecLink reparse data, and PowerShell `.ps1` targets from their exact script bytes. `.cmd` and `.bat` files are not admitted as command targets. WDA establishes the current path and typed identity again before mutation or Sandbox staging; the launch layer holds the reviewed target stable through process creation, and PowerShell-script targets are additionally launched through an independently sealed Windows-owned Windows PowerShell interpreter. Missing identity material is invalid input; changed identity returns `stale_plan` with `execution_started:false` and requires a fresh plan.

The expected executable path/kind/fingerprint fields are identity/staleness preconditions only. They are not evidence that a person approved anything and do not replace Claude/Codex permission authority.

After identity validation, the already-established current absolute target path is carried into execution without a later target PATH lookup. Regular-file, App Execution Alias, and PowerShell-script target identity is held stable through process creation; PowerShell scripts also bind the runtime-owned system interpreter separately at use time. External probes that do not have a prior reviewed plan still snapshot their current typed command-target identity immediately before launch and hold that same identity through process creation, so they do not leave an unguarded resolve-to-spawn replacement window.

The plugin's Python bootstrap does not find Python from the active project or inherited PATH. It uses Windows installation authorities/standard host locations or an explicit absolute `WINDOWS_DEV_AGENT_PYTHON` override, requires Python 3.11+, and runs the interpreter in isolated mode. Codex command hooks are themselves invoked through `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` rather than a bare `powershell.exe`, because Codex runs hook commands with the active request directory as their working directory. The initial Codex MCP stdio bootstrap is a narrower host limitation: current Codex plugin configuration roots relative `cwd` but does not portably expand a Windows system-root placeholder in `command`, so `.mcp.codex.json` still names `powershell.exe`. WDA does not claim that first host-resolved launcher is system-root-bound; after startup, WDA-owned control-plane launches are independently rooted.

Windows-owned control-plane executables used by the runtime—such as WSL, Windows Sandbox, and Windows PowerShell discovery—are resolved from the Windows system installation instead of PATH.

### Project scope is not permission to follow links anywhere

Claude project-scoped reads are bounded to the host project. Codex receives its host-attested project `cwd` through hook events rather than the MCP process; when the bundled Codex hooks are trusted, `PreToolUse` independently denies project-scoped calls whose requested directory resolves outside that host cwd. Without trusted Codex hooks, the required absolute project path is caller-selected and remains on Codex's prompt surface, so host-attested project confinement is not claimed for that fallback path. Within an established project boundary, WDA rejects symbolic-link/NTFS-reparse traversal at the relevant read/staging boundary. Project JSON is consumed from the same use-time verified handle whose final path is checked, and Windows Sandbox payload staging revalidates file/directory identities and budgets while copying. Failure to establish reparse metadata is not treated as proof that a path is ordinary.

Dev Container routing requires an actual project `.devcontainer/devcontainer.json` or root `.devcontainer.json`; an empty `.devcontainer/` directory is not configuration evidence, and linked/reparse configuration is rejected at the WDA read boundary.

This does not claim that every arbitrary command an approved tool executes is unable to traverse links on its own. The containment guarantee applies to WDA-owned project reads and Windows Sandbox payload staging.

### ACT is not outcome

Launching or requesting an external operation is not evidence that its intended effect occurred. WDA preserves separate states for planned, stale/rejected, started, completed, failed, timed-out, and unresolved external execution where the available observation supports those distinctions. A `stale_plan` is a `not_executed` outcome, not a failed execution.

Best-effort process-tree termination after timeout is not proof that every descendant or external side effect was undone.

## Windows Sandbox

The `untrusted_windows` route is the strongest isolation boundary WDA currently implements. It requires Windows Sandbox and explicit workspace-relative payload staging. The mapped payload share is read-only, the `.wsb` file is kept outside the mapped share, and WDA disables vGPU, networking, audio input, video input, printer redirection, and clipboard redirection for this route.

This reduces exposure; it does **not** claim Windows Sandbox is immune to sandbox escapes or host vulnerabilities. Keep Windows patched and do not use WDA's Sandbox wrapper as a substitute for a higher-assurance isolation platform when your threat model requires one.

WSL is a compatibility/interoperability route, not hostile-Windows containment. A Dev Container is a project reproducibility boundary, not a substitute for Windows Sandbox against an untrusted Windows executable.

## Persistent data and privacy

WDA persistent/control state is intentionally small:

- a bounded environment cache plus tiny mutation-generation/lock metadata;
- a bounded audit log plus one rotated predecessor and a tiny interprocess lock file;
- temporary WDA-owned Windows Sandbox bundles while needed for execution/cleanup.

Audit records do not persist raw commands, MCP arguments, stdout/stderr, or arbitrary tool responses. `logs_query` does not return the physical data-directory path.

Project MCP configuration may contain secrets. WDA's MCP audit summarizes structural metadata and does not return raw command strings, URLs, argument values, or environment values. Do not attach private configuration files to public security reports.

## Out of scope / known limitations

The following are not presently claimed as security guarantees:

- protection against a compromised Claude Code or Codex host;
- protection after the user/host deliberately authorizes an arbitrary destructive command outside WDA's forbidden classifier;
- rollback of arbitrary package installs or external mutations;
- Hyper-V isolation (not an implemented backend);
- successful completion of the inner Windows Sandbox command merely because the Sandbox process launched;
- the `WindowsSandbox.exe` process handle being a universal Windows Sandbox session-lifetime oracle;
- modern-MCP-protocol support beyond the legacy protocol surface currently exercised by the host adapters;
- system-root binding of the initial Codex MCP `powershell.exe` bootstrap while Codex's plugin schema/parser lacks a portable Windows-system command placeholder; trusted hooks and WDA-owned post-bootstrap Windows control-plane launches are rooted independently;
- resistance to an operating-system vulnerability that defeats the underlying Windows security boundary.

Please report cases where WDA's implementation violates the narrower guarantees it does make: project/data authority, reviewed-to-executed executable identity, request validation, outcome honesty, isolation routing, bounded retention, or immutable release identity.
