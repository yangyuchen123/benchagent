# Generated Constraint-Following Fixture — Manual No-MCP Repair

- **Environment ID:** `generated-constraint-following-fixture-v1-no-mcp`
- **Task ID:** `instruction-following-constraint-satisfaction-001`
- **Protocol:** `octagon.env.v1`
- **Timeout:** 600 seconds
- **Status:** manually repaired derivative; Benchmark Forge system and knowledge base were not changed

## Agent task

Read `materials/constraint_task.json` and produce exactly one final artifact at
`artifacts/final_output.json`. Preserve task intent while satisfying explicit and
implicit constraints, priority/precedence rules, distractor rejection, and valid
late-injected instructions.

Before finishing, check the artifact against
`schemas/final_output.schema.json` using capabilities already available to the Agent.
No benchmark-provided validator or MCP tool is required.

## Tool boundary

This environment registers zero benchmark-owned tools:

```text
tool_count = 0
entrypoints.mcp = absent
```

Artifact and constraint validation happen offline in `scorer.py` after submission.
The Agent may use its own runtime tools, such as file reading, shell, Python, or a
built-in JSON validator, but no particular validation transport is mandatory.

## Material and artifact

The required read-only fixture is `materials/constraint_task.json`. The canonical
artifact schema is `schemas/final_output.schema.json`.

Write exactly one JSON file at `artifacts/final_output.json`. The top-level object
must contain exactly one of:

- `cases`: at least six `{case_id, response}` objects with no extra fields;
- `clarification`: a non-empty string, optionally with `safe_degradation`.

## Provenance

Derived manually on 2026-08-27 from:

```text
artifact-schema-case-coverage-replay-20260827/bundle
```

Reason: the original environment inherited a knowledge-base pattern that required a
benchmark-provided `validate_output` MCP tool only to constrain JSON output. That
transport requirement was unrelated to the primary constraint-following capability
and created an avoidable infrastructure confound.
