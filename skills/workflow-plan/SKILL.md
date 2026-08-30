---
name: workflow-plan
description: Build a Windows-aware execution plan when a task has consequential dependencies, multiple phases, uncertainty that can change the route, or mutation that needs explicit verification and rollback.
---

# Workflow Plan

Use planning when it changes execution quality. A trivial read or obvious single reversible command does not need a multi-phase ceremony.

## Procedure

1. **Bind the task.** State the requested outcome, exact project/host boundary, hard constraints, and what would count as success.
2. **Inspect discriminating state.** Use `env_inspect`, `tool_discover`, or project-local reads only for facts that can change the route.
3. **Get the runtime scaffold.** Call `workflow_plan` with the task and relevant context. In Codex, also pass the current session/project directory in `cwd`; bundled plugin MCP processes are rooted in the plugin cache and must not infer that cache as the project.
4. **Resolve the route.** Choose the smallest coherent path that satisfies the task. If materially different mechanisms remain live, name the discriminator; do not manufacture cosmetic alternatives.
5. **State execution phases.** For each consequential phase include:
   - entry state actually required;
   - concrete action/tool;
   - safety class;
   - observable exit condition;
   - rollback only when an explicit restore path exists.
6. **Execute under the active host permission boundary.** Claude uses its tightening-only `PreToolUse` adapter; Codex uses native MCP/tool approval policy and a deny-only forbidden-action hook. Never emulate one host's permission result inside the other.
7. **Verify before continuing.** A phase that changes upstream state invalidates downstream assumptions that depended on it; re-evaluate those dependencies rather than blindly replaying the original plan.

## Stop conditions

- Stop planning when further decomposition cannot change the next action or verification surface.
- If one missing fact selects between materially different routes, obtain that fact rather than expanding the plan.
- If execution is blocked, return the exact blocker and the smallest fact or authorization needed to continue.
- Do not call a task complete until the requested post-state is observed.
