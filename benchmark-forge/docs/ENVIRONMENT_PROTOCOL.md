# Executable benchmark environment protocol

## Why this exists

A behavior benchmark must not be reduced to a multiple-choice question merely
because the generation schema only knows `question/options/answer`. Benchmark
Forge therefore supports two item forms:

- `static_question`: closed knowledge evaluation;
- `executable_task`: an open task run by an agent inside an environment.

For agent behavior goals, `executable_task` is the default design intent.

## `octagon.env.v1`

The protocol follows the stable concepts already present in
`agent-octagon-envs` while preserving repository boundaries. Benchmark Forge
creates or references the contract; `eval-system` materializes and runs it;
`agent-eval` consumes artifacts and scoring evidence.

An executable task contains:

```text
ExecutableTaskContract
├── instruction                 open agent instruction
├── context / constraints
├── EnvironmentContract
│   ├── implementation ref      path/git/registry/generated reference
│   ├── prerequisites
│   ├── entrypoints             MCP / native / CLI
│   ├── tools
│   ├── materials               source → workspace target, normally read-only
│   ├── workspace policy        isolation/network/write/forbidden paths
│   ├── timeout
│   └── maturity                existing/adapted/generated_contract/pending
├── ArtifactRequirement[]       files or environment-state outputs
├── ScoringContract
│   ├── weighted dimensions
│   ├── evidence sources        artifact/state/tool trace/trajectory/verifier
│   ├── pass threshold
│   └── scorer reference
└── observation_requirements
```

## Security and storage boundary

The generation agent receives normalized profiles, public task specs and RAG
chunks. It does not receive a mounted environment repository. `private/`,
expected answers and scorer implementation are not placed in its workspace.
References are resolved later by `eval-system` under its own permissions.

## Provider mapping

`OctagonEnvironmentProvider` exposes public Octagon task specs through the
normal `SourceProvider` protocol with source mode `existing_environment`.
For a single environment its provider ID equals the environment ID, avoiding an
ambiguous environment-ID/provider-ID mapping. Samples include only:

- normalized environment profile;
- public task spec;
- task reference;
- environment reference.

## Verification states

Contract validation is not execution validation. An existing environment task
may pass contract verification, but missing checksums, unresolved references or
absence of an actual TrialResult must produce warnings. A benchmark containing
such items is `degraded`, not `completed`, until runtime verification exists.
