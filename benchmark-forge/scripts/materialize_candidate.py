from __future__ import annotations
import os
import sys
from pathlib import Path
from benchmark_forge import PydanticAIRoleAgents, openai_compatible_model
from benchmark_forge.octagon import OctagonKnowledgeBase
from benchmark_forge.staging import EnvironmentCandidateRegistry, write_scaffold


def load_env(path: Path):
    for raw in path.read_text(encoding='utf-8').splitlines():
        if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
            k,v=raw.split('=',1); os.environ.setdefault(k.strip(),v.strip())

if len(sys.argv) != 3:
    raise SystemExit('usage: materialize_candidate.py REGISTRY_ROOT CANDIDATE_ID')
load_env(Path('../.env'))
registry=EnvironmentCandidateRegistry(sys.argv[1])
candidate=registry.load(sys.argv[2])
model=openai_compatible_model(model_name=os.environ['LLM_MODEL'],base_url=os.environ['LLM_API_BASE_URL'],api_key=os.environ['LLM_API_KEY'])
agents=PydanticAIRoleAgents(model=model,knowledge_base=OctagonKnowledgeBase('run/octagon.sqlite3'))
bundle=agents.materialize_environment(candidate.item)
root,validation=write_scaffold(registry,candidate.candidate_id,bundle)
print({'root':str(root),'valid':validation.valid,'errors':validation.errors,'warnings':validation.warnings,'files':len(bundle.files),'status':registry.load(candidate.candidate_id).status.value})
