---
description: Build a structured execution plan for a consequential or multi-step Windows development task before mutation.
---

$ARGUMENTS is the task description.

Use the `workflow_plan` MCP tool to establish the deterministic capability-aware scaffold, then refine only the parts that require task reasoning.

## Procedure

1. Bind the requested outcome, exact target, hard constraints, and observable success condition.
2. Inspect only environment/project state that can change the route (`env_inspect`, `tool_discover`, or project-local reads).
3. Call `workflow_plan` with the task and relevant context.
4. Resolve the best-fit route. Generate alternatives only when they differ in mechanism or consequence enough to change the decision.
5. For each consequential phase state:
   - entry condition;
   - exact action/tool;
   - safety class: `read-only`, `reversible`, `approval-required`, `checkpoint`, or `forbidden`;
   - observable exit condition;
   - rollback only when a real restore path exists.
6. Present the plan before consequential mutation. Do not duplicate Claude Code's host permission prompt with a fake internal confirmation mechanism.
7. During execution, re-check downstream assumptions when an upstream phase changes the state they depend on.

Do not plan past the point where further decomposition cannot change the next action or verification surface.
