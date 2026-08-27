# Environment candidate staging and promotion

Generated environment contracts are **not** canonical Octagon environments.
Benchmark Forge never writes them directly into `agent-octagon-envs`.

## Lifecycle

```text
generated_contract
  → static_validated
  → scaffolded
  → smoke_tested
  → pilot_scored
  → approved
  → promotion_ready
  → external reviewed import into agent-octagon-envs
```

A technical validation failure moves the candidate to `needs_repair`; an explicit policy/human rejection moves it to `rejected`; the evidence remains
in the candidate registry for debugging.

## Storage

Natural-language generation stages candidates under its own run artifact:

```text
<artifact-root>/environment-candidates/<candidate-id>/candidate.json
```

This registry is separate from `agent-octagon-envs`. It contains the generated
contract, validation records, eval-system TrialResult references, agent-eval
report references and human approval.

## Default promotion gates

All of the following checks must pass:

- `contract_schema`
- `provenance_safety`
- `scaffold_integrity`
- `environment_smoke`
- `scorer_smoke`
- `artifact_collection`

Additionally:

- at least two successful eval-system pilot trials;
- at least two distinct target agents;
- agent-eval benchmark quality >= 60;
- agent-eval human alignment >= 60;
- explicit human approval.

These are canonical-repository gates, not MVP generation gates. A generated
benchmark may remain `degraded` and useful for iteration while still being
ineligible for promotion.

## Promotion output

`build_promotion_bundle()` only writes `promotion-bundle.json`. It does not copy
files into `agent-octagon-envs`. Import remains an explicit reviewed external
operation so repository ownership and permissions stay clear.
