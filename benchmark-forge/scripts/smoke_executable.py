from __future__ import annotations
import os
from pathlib import Path
from benchmark_forge import BenchmarkOrchestrator, PydanticAIRoleAgents, RunConfig, UserGoal, openai_compatible_model
from benchmark_forge.octagon import EnvironmentCatalog, OctagonEnvironmentProvider, OctagonKnowledgeBase


def load_env(path: Path):
    for raw in path.read_text(encoding='utf-8').splitlines():
        if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
            k,v=raw.split('=',1); os.environ.setdefault(k.strip(),v.strip())

load_env(Path('../.env'))
catalog=EnvironmentCatalog('/home/yang/agent-octagon-envs')
kb=OctagonKnowledgeBase('run/octagon.sqlite3')
provider=OctagonEnvironmentProvider(catalog,['agent-parallel-scheduling'])
model=openai_compatible_model(model_name=os.environ['LLM_MODEL'],base_url=os.environ['LLM_API_BASE_URL'],api_key=os.environ['LLM_API_KEY'])
benchmark=BenchmarkOrchestrator(
    agents=PydanticAIRoleAgents(model=model,knowledge_base=kb),
    config=RunConfig(model_id=os.environ['LLM_MODEL']),
).run(
    UserGoal(goal_id='exec-smoke',description='生成一个测试 Agent 真实并行工具调度和依赖保真的开放可执行 benchmark',target_size=1),
    [provider],benchmark_id='exec-smoke',artifact_root='run/exec-smoke'
)
print({'status':benchmark.status.value,'items':len(benchmark.items),'rejected':len(benchmark.rejected_items),'warnings':benchmark.warnings})
for item in benchmark.items+benchmark.rejected_items:
    print({'item_kind':item.item_kind,'answer_type':item.answer_type,'has_executable_task':item.executable_task is not None,'item_warnings':item.warnings})
