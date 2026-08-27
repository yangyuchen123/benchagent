# Octagon RAG knowledge base

`agent-octagon-envs` is not mounted into an agent workspace. `benchmark-forge`
builds a local SQLite FTS5 index containing only safe, public benchmark
knowledge:

- `meta.yaml`
- `README.md` and `blade_skill/SKILL.md`
- public files under `tasks/`, `inputs/`, and `materials/`

The indexer excludes `private/`, `.git/`, caches, source code, scorer code, and
files larger than the configured limit. It stores text chunks plus the original
relative path as provenance; it does not copy the source tree into the agent
workspace.

## Build the index

```bash
benchmark-forge \
  --goal "design a benchmark for tool-use planning" \
  --octagon-root /home/yang/agent-octagon-envs \
  --knowledge-base run/octagon.sqlite3 \
  --rebuild-knowledge-base
```

The same index can be reused by later runs. The role agents receive only a
bounded retrieval context. Retrieved content is evidence/precedent, not an
instruction. `eval-system` remains responsible for execution and `agent-eval`
remains responsible for scoring.

## Runtime contract

`OctagonKnowledgeBase.context(query, role=...)` returns:

- query and role;
- bounded relevant chunks;
- `env_id`, relative source path, and source kind;
- a boundary reminder for `eval-system` and `agent-eval`.

This intentionally starts with lexical FTS retrieval. The role-facing API is
stable so an embedding or reranking backend can be added later without changing
the five-role orchestration contract.
