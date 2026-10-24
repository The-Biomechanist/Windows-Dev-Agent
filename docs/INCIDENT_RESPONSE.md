# Incident response design

Goal: help a person with no technical background investigate a suspected compromise on their Windows machine, with as little friction as possible, while keeping the machine safe to act on without constant supervision.

Status: the Python core (evidence scoring, plan validation, execution, undo, and the gate) is unit-tested on Linux. The PowerShell operations in `src/incident/ops.ps1` are **unverified**: they have not been run on Windows, and they must be before this is relied on.

## Where autonomy comes from

Autonomy comes from bounded authority, not from trusting the model.

| Tier | What it covers | Prompt | Mechanism |
|---|---|---|---|
| Autonomous | `incident_collect`: read-only evidence collection | None | Gate returns `allow`; the only `allow` the plugin issues |
| Reviewed | `incident_execute` with `execute: true` | One host prompt for the whole plan | Gate asks; the prompt shows the plan summary; the runtime runs exactly that object |
| Reviewed | `incident_undo` with `execute: true` | One host prompt | Gate asks |
| Free | Planning and dry runs (`execute: false`) | None; changes nothing | Gate defers |

## Invariants

1. **Evidence is read-only.** Collection writes one evidence record under the plugin data directory and nothing else.
2. **The approved object is the executed object.** The host prompt shows the full plan. The runtime validates that plan and records its digest. `expected_digest` lets the person's review be checked against what runs.
3. **Targets come from observed evidence.** A plan may name only a path, process, startup entry, scheduled task, or address that the most recent evidence (under 30 minutes old) observed. The model cannot name a target it did not see.
4. **Only reversible primitives exist.** There is no action for deleting files or registry keys, deleting services or tasks, changing Defender or other security tooling, changing system policy, or sending data out.
5. **Protected things are never targets.** Windows, Program Files, and Defender paths are refused. So are critical system processes (`lsass`, `csrss`, `MsMpEng`, and others). Private, loopback, link-local, multicast, and reserved addresses are never blocked, so local networking cannot be cut off.
6. **Manifest before action.** Each step is written to the manifest as `running` before it touches the machine, and to `done`, `failed`, or `unknown` after. A timeout is `unknown`, never `failed`, because the effect is not known.
7. **Stop on the first failure.** Nothing continues past a failed or unknown step.
8. **Host data is untrusted.** Every string from the machine is length-capped and stripped of control characters before display. The skill and the tool descriptions say never to treat it as an instruction.
9. **Accounts are guidance only.** Password resets, session revocation, and bank contact are instructions to the person. The plugin never automates them.

## Primitives

| Action | Operation | Undo |
|---|---|---|
| `quarantine_file` | Move the file into `incident/quarantine/<execution>/`, recording its SHA-256 | Move it back if the original location is free and the hash matches |
| `stop_process` | Stop a process after checking pid, name, path, and start time | None: the process must be started again by hand |
| `disable_run_value` | Save an HKCU or HKLM Run value, then remove it | Write the saved value back |
| `disable_scheduled_task` | Disable a task that is observed as enabled | Enable it again |
| `block_program_outbound` | Create a named outbound firewall block rule for a program with observed connections | Remove the rule |
| `block_remote_address` | Create a named outbound block rule for one observed public IP address | Remove the rule |

Firewall rule names are derived from the target, so a rule can be found and removed without guessing.

## Evidence

`ops.ps1 collect` gathers, in one read-only pass:

- running processes with path, signature, start time, and outbound connections (capped at 400)
- startup entries: Run keys, scheduled tasks (not disabled), Startup folders, and automatic services
- Windows Defender real-time status and recent detections
- the system proxy setting
- the count and a sample of non-default hosts-file entries

It does not collect command lines, file contents, browser data, credentials, or keystrokes.

Indicators are leads, not verdicts:

- **high**: an unsigned program running from a user folder with network activity; an unsigned autostart entry in a user folder; Defender real-time protection off; recent Defender detections
- **notice**: an unsigned program in a user folder without network activity; an unsigned service in a user folder (reported only); a system proxy; hosts-file entries; network activity from a program whose location Windows will not reveal

## Undo

Each executed step records what undo needs. `incident_undo` reverses completed steps newest first, once per execution. A second undo is refused. Partial undo failures are reported per step.

## Limits

- This is not an antivirus engine. Detection of unknown malware still depends on Microsoft Defender or another real scanner. The plugin's job is to show the person what is running, what starts automatically, and what changed.
- Signature checks treat "not signed by a trusted publisher" as a reason to look, not as a verdict.
- Services and `.lnk` shortcuts are reported, not resolved or changed.
- Stopping a process is the one change that cannot be undone. Stopping it does not keep it stopped: a startup entry starts it again at the next sign-in. A plan that stops a process should also remove its startup entry, and the skill asks for that.
- On a non-elevated session, some probes and all firewall operations fail. The plugin reports those failures instead of hiding them.
- Codex has no autonomous tier. Its adapter prompts for every incident tool until the behavior is verified in a live install.

## Verification still owed

1. Run `src/incident/ops.ps1` on Windows against a benign test file and a benign test process, and confirm each operation's result and undo.
2. Confirm that `Get-AuthenticodeSignature` results match Windows behavior for Microsoft-signed and unsigned binaries.
3. Confirm that a non-elevated `collect` returns `degraded` with warnings rather than failing.
4. Confirm the Claude `allow` for `incident_collect` in a live headless session, and that `incident_execute` shows the plan summary in the approval prompt.
