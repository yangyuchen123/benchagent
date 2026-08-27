
# Agent-driven scorer design

## Why scorer generation is not a static lint problem

`scorer.py` is an executable measurement instrument. Syntax checks can prove
that it loads, but they cannot prove that it measures the capability described
by the benchmark. In particular, a scorer may:

- read the wrong workspace even though its function signature is valid;
- require internal identifiers that were never disclosed to the evaluated Agent;
- trust an Agent-authored coordination log instead of runtime evidence;
- fail when one optional trace channel is missing even though equivalent native
  runtime evidence exists;
- reward artifact presence while failing to measure task decomposition,
  assignment, parallel coordination, or acceptance behavior.

These are construct-validity problems. Adding another AST or string rule for
each observed failure only overfits Benchmark Forge to previous incidents.

## Role ownership

No sixth role is introduced. The original roles remain:

```text
Design
Grounding
Allocation
Executor
Verification & Control
```

The environment phase now uses the existing roles as follows:

```text
Verification & Control
  → ScorerDesign
      - public scoring contract
      - multiple implementation options per dimension
      - evidence authority and fallback order
      - workspace-resolution options
      - adversarial calibration cases

Executor
  → EnvironmentScaffoldBundle including scorer.py

Verification & Control
  → ScorerReview(pass | repair | reject)
      - construct coverage
      - public satisfiability
      - runtime grounding
      - evidence fallback
      - calibration behavior

Executor (bounded, only when requested)
  → repaired EnvironmentScaffoldBundle
```

This is an internal collaboration between two original roles, not a new global
pipeline role.

## Multiple implementation options

A scoring dimension must not be coupled prematurely to one evidence channel.
`ScorerDesign` requires at least two implementation options for every dimension
it plans. Examples include:

```text
canonical eval-system/AgentOctagon runtime records
correlated native tool/subagent events
attempt workspace artifacts resolved through env_db + attempt_id
environment state transitions
independent deterministic artifact verifier
Agent-authored coordination log (auxiliary only)
```

The final scorer may combine options or use a semantically equivalent method.
The design is not a code template. It defines the construct and acceptable
observable evidence.

Recommended evidence authority for subagent benchmarks:

```text
runtime_canonical
  → runtime_correlated
  → artifact_observed
  → derived
  → agent_self_report
```

A lower-authority source may fill a documented gap, but should not overwrite
contradictory higher-authority evidence.

## Deterministic validation remains, but only as a safety floor

Static validation still rejects unsafe or unloadable artifacts:

- missing required files;
- invalid YAML/JSON/Python;
- invalid environment names;
- path traversal;
- host paths or credentials;
- missing `score(...)` entrypoint.

Heuristics such as “the scorer does not appear to load `attempt_id`” or “a
subtask id appears only in scorer code” are now warnings sent to semantic
review. They are not treated as universal proofs because valid environments can
resolve evidence through different runtime adapters.

## Candidate lifecycle gate

Canonical promotion now additionally requires:

```text
scorer_semantic_review = passed
```

The review and design are persisted under:

```text
<environment-candidate>/validation/scorer-design.json
<environment-candidate>/validation/scorer-review-N.json
```

A `repair` or `reject` verdict puts the candidate in `needs_repair`. A later
passing review can clear that state when no other failed check remains. Static
success, `scoring_status=completed`, or a numeric score alone cannot satisfy
this gate.

## Calibration expectations

Before a long pilot, Verification & Control should reason over at least these
cases:

1. strong execution with complete runtime and artifact evidence;
2. weak execution that creates plausible files but does not perform the target
   behavior;
3. fabricated self-report without matching runtime evidence;
4. partial wire capture with complete native/correlated runtime evidence;
5. completed runtime behavior with missing required artifacts;
6. valid use of public work-package names that differ from internal design ids.

After a pilot, the same scorer should be run against frozen evidence. Repairing
and rescoring frozen evidence is preferred over immediately spending another
nine-minute Agent run.
