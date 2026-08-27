from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; FORGE=ROOT/'benchmark-forge'; REG=ROOT/'preference-registry'
sys.path[:0]=[str(FORGE/'src'),str(REG/'src')]
for raw in (ROOT/'.env').read_text().splitlines():
    if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
        k,v=raw.split('=',1); os.environ.setdefault(k.strip(),v.strip())
from benchmark_forge.preference_alignment import BenchmarkPlanCandidate
from benchmark_forge.pydantic_ai_adapter import PydanticAIRunner,openai_compatible_model
from benchmark_forge.planning_pair import DoublePlanningService
from preference_registry.models import ComparisonCriterion,ComparisonRequest,ComparisonSchema,ContentBlock,ReviewPackage,SubjectSnapshot
from preference_registry.repository import RegistryRepository
from preference_registry.security import TokenSigner
from preference_registry.services import RegistryService
GOAL='设计一个开放可执行的 benchmark，评测主 agent 调用自己的 subagent 完成任务拆解、任务指派、并行协作、任务验收、失败修复和最终整合。benchmark 必须基于工具调用、运行轨迹、工作区产物和可复核的验收证据评分，不能退化成选择题或只看最终答案。'
CONTEXT='subagent-coordination-initial-flywheel-v1'; DB=Path('/tmp/benchagent-e2e/preference-flywheel-v1.db'); OUT=Path('/tmp/benchagent-e2e/preference-flywheel-v1')
INSTRUCTIONS='''你是 Benchmark Forge 的 Plan Designer。只设计 benchmark 方案，不生成 meta.yaml、core.py、scorer.py，也不执行环境物化。返回一个 BenchmarkPlanCandidate。目标是评测主 agent 委派 subagent 的真实能力：任务拆解、任务指派、并行协作、验收、失败修复、最终整合。方案必须是开放可执行任务，不得是选择题、知识问答或只比较最终答案。必须明确真实工具/入口、可观察 runtime trace、工作区 artifacts、subagent 边界、任务依赖、验收与修复循环、最终正确性。behavior_requirements 写可观察行为；artifact_requirements 写公开产物；scoring_intent 写如何依据轨迹和产物评分及防止自报；difficulty_intent 说明长任务和故障扰动。不要编造已经存在的环境、工具、数据集或 eval-system 运行结果。只返回结构化 BenchmarkPlanCandidate。'''
CRITERIA=[
 ComparisonCriterion(criterion_id='target_alignment',title='目标对齐',order=1,question='哪个方案更准确地评测主 agent 的 subagent 任务拆解、指派、协作、验收和修复，而不是泛化成普通问答？',description='关注能力覆盖、任务边界和与用户目标的一致性。'),
 ComparisonCriterion(criterion_id='open_agent_behavior',title='开放 Agent 行为',order=2,question='哪个方案更像真实开放的 agent benchmark，能迫使 agent 使用工具、管理状态并进行多步决策，而不是选择题或静态文本题？',description='关注工具调用、环境状态、长任务、并行/依赖和故障处理。'),
 ComparisonCriterion(criterion_id='evidence_and_scoring',title='证据与评分',order=3,question='哪个方案更容易依据真实轨迹和独立产物进行可信评分，并能区分自报、伪造日志和真正完成？',description='关注 runtime evidence、artifact provenance、验收证据和 scorer 可实现性。'),
 ComparisonCriterion(criterion_id='benchmark_design_quality',title='整体设计质量',order=4,question='如果交给工程师继续物化，哪个方案更清晰、完整、可控，后续更可能成为高质量 benchmark？',description='关注复杂度、契约完整性、失败模式和实现成本。')]
SCHEMA=ComparisonSchema(schema_id='subagent-coordination-plan-review-v1',subject_type='benchmark-plan',stage='pre_materialization',criteria=CRITERIA)
def snap(plan,ref):
 p=plan.model_dump(mode='json'); blocks=[ContentBlock(block_id='overview',title='方案概览',kind='markdown',content=plan.title+'\n\n'+plan.task_description),ContentBlock(block_id='environment',title='环境与工具',kind='markdown',content=plan.environment_description),ContentBlock(block_id='behaviors',title='可观察行为',kind='json',content=plan.behavior_requirements),ContentBlock(block_id='artifacts',title='公开产物',kind='json',content=plan.artifact_requirements),ContentBlock(block_id='scoring',title='评分意图',kind='json',content=plan.scoring_intent),ContentBlock(block_id='difficulty',title='难度与成本',kind='markdown',content=plan.difficulty_intent+'\n'+plan.cost_intent)]
 review=ReviewPackage(subject_id=ref,subject_type='benchmark-plan',context_key=CONTEXT,title=plan.title or 'Benchmark plan',summary=plan.capability,blocks=blocks)
 return SubjectSnapshot(subject_ref=ref,subject_type='benchmark-plan',context_key=CONTEXT,version='1',review_package=review)
def main():
 if OUT.exists(): shutil.rmtree(OUT)
 OUT.mkdir(parents=True)
 model=openai_compatible_model(model_name=os.environ['LLM_MODEL'],base_url=os.environ['LLM_API_BASE_URL'],api_key=os.environ['LLM_API_KEY'])
 runner=PydanticAIRunner(model=model,output_type=BenchmarkPlanCandidate,instructions=INSTRUCTIONS,timeout=180,retries=2); count=0
 def generate(prompt):
  nonlocal count; count+=1; return runner.run_sync(prompt).model_copy(update={'plan_id':f'flywheel-plan-{count:02d}'})
 planner=DoublePlanningService(generator=generate,model_id=os.environ['LLM_MODEL'],knowledge_snapshot='plan-only-no-materialization-v1',similarity_threshold=.92,max_resamples=1)
 service=RegistryService(RegistryRepository(DB),token_signer=TokenSigner('flywheel-secret-at-least-16')); pairs=[]
 for i in range(1,6):
  pair=planner.generate_pair(GOAL,pair_id=f'flywheel-pair-{i:02d}'); aref,bref=f'flywheel-{i:02d}-a',f'flywheel-{i:02d}-b'
  req=ComparisonRequest(producer='benchmark-forge',producer_version='0.1.0-plan-only',subject_type='benchmark-plan',context_key=CONTEXT,stage='pre_materialization',subject_a=snap(pair.plan_a,aref),subject_b=snap(pair.plan_b,bref),comparison_schema=SCHEMA)
  rec=service.submit_request(req); ass=service.claim_assignment(reviewer_pseudonym='engineer-initial',request_id=rec.request_id,presentation_seed=f'flywheel-seed-{i:02d}')
  pairs.append({'pair':pair.model_dump(mode='json'),'request':req.model_dump(mode='json'),'assignment':ass.model_dump(mode='json')}); print(f'completed {i}/5 pair={pair.pair_id} similarity={pair.similarity_score:.3f}',flush=True)
 data={'schema_version':'benchmark-forge.plan-preference-batch.v1','goal':GOAL,'context_key':CONTEXT,'comparison_schema':SCHEMA.model_dump(mode='json'),'pairs':pairs}; (OUT/'collection.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
 lines=['# Benchmark Plan Preference — 初始数据飞轮','','请对下面 5 组方案分别回答 4 个结构化问题。每组只选 A 或 B，也可以选择 tie / both_bad / not_enough_information。','',f'目标：{GOAL}','']
 for i,item in enumerate(pairs,1):
  ass=item['assignment']; lines += [f'## 第 {i} 题','','### 方案 A',ass['left_package']['title'],ass['left_package']['summary'],'']
  for b in ass['left_package']['blocks']: lines += [f"**{b['title']}**",json.dumps(b['content'],ensure_ascii=False,indent=2) if b['kind']=='json' else str(b['content']),'']
  lines += ['### 方案 B',ass['right_package']['title'],ass['right_package']['summary'],'']
  for b in ass['right_package']['blocks']: lines += [f"**{b['title']}**",json.dumps(b['content'],ensure_ascii=False,indent=2) if b['kind']=='json' else str(b['content']),'']
  lines += ['### 请选择','1. 目标对齐：A / B / tie / both_bad / not_enough_information','2. 开放 Agent 行为：A / B / tie / both_bad / not_enough_information','3. 证据与评分：A / B / tie / both_bad / not_enough_information','4. 整体设计质量：A / B / tie / both_bad / not_enough_information','']
 (OUT/'human-review.md').write_text('\n'.join(lines)); print(json.dumps({'output':str(OUT),'db':str(DB),'questions':5,'criteria_per_question':4},ensure_ascii=False))
if __name__=='__main__': main()
