from __future__ import annotations

from benchmark_forge import (
    ArtifactRequirement, BenchmarkItem, ContentReference, EnvironmentContract,
    ExecutableTaskContract, ScoringContract, ScoringDimensionContract,
    ToolContract, WorkspaceContract,
)
from benchmark_forge.environment_ir import IRComponentFile, IRComponentOutput, lower_contract_to_ir
from benchmark_forge.materialization_workflow import (
    BundleTestResult, FailureObservation, MaterializationPolicy,
    MaterializationWorkflow, RepairPlan,
)


def _item() -> BenchmarkItem:
    contract = ExecutableTaskContract(
        task_id="workflow-task",
        instruction="Use the environment and produce artifacts/final.json.",
        environment=EnvironmentContract(
            environment_id="workflow-environment-v1",
            tools=[ToolContract(name="read_case", interface="mcp", entrypoint={
                "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
            })],
            entrypoints={"mcp": {"transport": "stdio", "command": ["python", "-m", "mcp_server"]}},
            workspace=WorkspaceContract(writable_paths=["artifacts"]),
            implementation=ContentReference(type="generated", ref="workflow-environment-v1"),
        ),
        artifacts=[ArtifactRequirement(path="artifacts/final.json", description="result")],
        scoring=ScoringContract(dimensions=[ScoringDimensionContract(
            name="completion", weight=100, description="complete", evidence_sources=["artifact"],
        )], pass_threshold=60),
        observation_requirements=["artifact"],
    )
    return BenchmarkItem(
        item_id="workflow-item", dimension_id="completion", item_kind="executable_task",
        covered_dimension_ids=["completion"], source_mode="generated_environment",
        source_id="workflow-provider", answer_type="artifact", executable_task=contract,
    )


def _output(component_id: str, *, bad_test: bool = False, bad_runtime: bool = False) -> IRComponentOutput:
    if component_id == "manifest":
        return IRComponentOutput(component_id="manifest", files=[
            IRComponentFile(path="meta.yaml", content="name: workflow-environment-v1\n"),
            IRComponentFile(path="README.md", content="generated"),
            IRComponentFile(path="tasks/workflow-task.json", content="{}"),
        ])
    if component_id == "runtime":
        content = "def broken(:\n" if bad_runtime else (
            "from octagon.env_api import EnvContext, env_tool\n"
            "@env_tool(name='read_case', description='read', parameters={'type':'object','properties':{}})\n"
            "def read_case(ctx: EnvContext):\n    return {'ok': True}\n"
        )
        mcp = (
            "import os, httpx\nfrom mcp.server.fastmcp import FastMCP\n"
            "mcp=FastMCP('test')\n"
            "ATTEMPT_ID=os.environ.get('OCTAGON_ATTEMPT_ID','')\n"
            "TOKEN=os.environ.get('OCTAGON_ENV_TOKEN','')\n"
            "BASE=os.environ.get('OCTAGON_BASE_URL','')\n"
            "@mcp.tool()\ndef read_case():\n"
            "    return httpx.post(f'{BASE}/attempts/{ATTEMPT_ID}/tools/read_case', headers={'Authorization':f'Bearer {TOKEN}'}, json={}).json()\n"
        )
        return IRComponentOutput(component_id="runtime", files=[
            IRComponentFile(path="core.py", content=content),
            IRComponentFile(path="mcp_server.py", content=mcp),
        ])
    if component_id == "scorer":
        return IRComponentOutput(component_id="scorer", files=[IRComponentFile(
            path="scorer.py",
            content=(
                "from pathlib import Path\n"
                "def score(*, attempt_id, task, env_db=None, trace=None, final_state=None, **kwargs):\n"
                "    workspace = Path(env_db).parent / 'skill_workspace' if env_db else Path('missing')\n"
                "    called = any(isinstance(row, dict) and row.get('tool_name') == 'read_case' for row in (trace or []))\n"
                "    return [{'dimension':'completion','value':0,'detail':f'missing artifact; called={called}; workspace={workspace}'}]\n"
            ),
        )])
    test = "from pathlib import Path\ndef test_bad():\n    assert Path('artifacts/final.json').is_file()\n" if bad_test else "def test_contract():\n    assert True\n"
    return IRComponentOutput(component_id="tests", files=[IRComponentFile(path="tests/test_contract.py", content=test)])


def test_workflow_repairs_only_static_failure_owner_and_becomes_ready(tmp_path):
    item = _item()
    ir = lower_contract_to_ir(item.executable_task).freeze()
    generated: list[str] = []
    repaired: list[str] = []

    def generate(*, component_id, **kwargs):
        generated.append(component_id)
        return _output(component_id, bad_test=component_id == "tests")

    def repair(*, component_id, **kwargs):
        repaired.append(component_id)
        return _output(component_id)

    workflow = MaterializationWorkflow(
        component_generator=generate, component_repairer=repair,
        policy=MaterializationPolicy(max_repairs_per_component=1),
    )
    bundle, report = workflow.run_components(item=item, ir=ir, output_root=tmp_path)

    assert bundle is not None
    assert report.status == "ready"
    assert report.manual_intervention_required is False
    assert generated == ["manifest", "runtime", "scorer", "tests"]
    assert repaired == ["tests"]
    assert report.metrics.automatic_diagnoses == 1
    assert report.metrics.repaired_components == 1
    assert (tmp_path / "workflow-report.json").exists()
    assert (tmp_path / "bundle" / "tests" / "test_contract.py").exists()


def test_workflow_reuses_checksum_bound_component_checkpoints(tmp_path):
    item = _item()
    ir = lower_contract_to_ir(item.executable_task).freeze()
    calls = 0

    def generate(*, component_id, **kwargs):
        nonlocal calls
        calls += 1
        return _output(component_id)

    first = MaterializationWorkflow(component_generator=generate)
    _, report1 = first.run_components(item=item, ir=ir, output_root=tmp_path)
    assert report1.status == "ready"
    assert calls == 4

    def must_not_generate(**kwargs):
        raise AssertionError("valid component checkpoints must be reused")

    second = MaterializationWorkflow(component_generator=must_not_generate)
    _, report2 = second.run_components(item=item, ir=ir, output_root=tmp_path)
    assert report2.status == "ready"
    assert report2.metrics.reused_components == 4
    assert report2.metrics.model_calls == 0


class _FailOnceTests:
    def __init__(self):
        self.calls = 0

    def run(self, bundle_root):
        self.calls += 1
        if self.calls == 1:
            return BundleTestResult(
                passed=False, returncode=1,
                output="tests/test_contract.py::test_runtime FAILED\nAssertionError: public runtime response invalid",
                failed_test_paths=["tests/test_contract.py"],
            )
        return BundleTestResult(passed=True, returncode=0, output="1 passed")


def test_ambiguous_test_failure_uses_diagnosis_agent_then_repairs_runtime(tmp_path):
    item = _item()
    ir = lower_contract_to_ir(item.executable_task).freeze()
    repaired: list[str] = []
    observations: list[FailureObservation] = []

    def generate(*, component_id, **kwargs):
        return _output(component_id)

    def diagnose(*, observation, **kwargs):
        observations.append(observation)
        return RepairPlan(
            action="repair_components", component_ids=["runtime"], confidence=.9,
            rationale="the test exercises public runtime behavior",
            repair_instructions=["repair the public response"],
        )

    def repair(*, component_id, **kwargs):
        repaired.append(component_id)
        return _output(component_id)

    workflow = MaterializationWorkflow(
        component_generator=generate, component_repairer=repair, diagnoser=diagnose,
        test_backend=_FailOnceTests(),
        policy=MaterializationPolicy(run_bundle_tests=True, max_repairs_per_component=1),
    )
    _, report = workflow.run_components(item=item, ir=ir, output_root=tmp_path)
    assert report.status == "ready"
    assert repaired == ["runtime"]
    assert observations[0].failure_kind == "bundle_tests"
    assert report.metrics.agent_diagnoses == 1
    assert report.metrics.bundle_test_attempts == 2


def test_ambiguous_failure_stops_without_silent_contract_or_ir_rewrite(tmp_path):
    item = _item()
    ir = lower_contract_to_ir(item.executable_task).freeze()

    workflow = MaterializationWorkflow(
        component_generator=lambda component_id, **kwargs: _output(component_id),
        test_backend=_FailOnceTests(),
        policy=MaterializationPolicy(run_bundle_tests=True),
    )
    _, report = workflow.run_components(item=item, ir=ir, output_root=tmp_path)
    assert report.status == "needs_repair"
    assert report.manual_intervention_required is True
    assert report.metrics.manual_interventions == 1
    assert report.ir_checksum == ir.ir_checksum


def test_fixed_contract_replay_reuses_ir_only_for_same_contract_checksum(tmp_path):
    from benchmark_forge.materialization_workflow import FixedContractReplayWorkflow

    item = _item()

    class Compiler:
        def __init__(self):
            self.calls = 0

        def compile(self, contract):
            self.calls += 1
            return lower_contract_to_ir(contract).freeze()

    compiler = Compiler()
    generated = 0

    def generate(*, component_id, **kwargs):
        nonlocal generated
        generated += 1
        return _output(component_id)

    replay = FixedContractReplayWorkflow(
        compiler=compiler, materializer=MaterializationWorkflow(component_generator=generate),
    )
    first = replay.run(item=item, output_root=tmp_path)
    assert first.status == "ready"
    assert compiler.calls == 1
    assert generated == 4

    second = replay.run(item=item, output_root=tmp_path)
    assert second.status == "ready"
    assert compiler.calls == 1
    assert second.metrics.reused_ir == 1
    assert second.metrics.reused_components == 4

    changed_contract = item.executable_task.model_copy(update={"instruction": "Changed accepted instruction."})
    changed_item = item.model_copy(update={"executable_task": changed_contract})
    third = replay.run(item=changed_item, output_root=tmp_path)
    assert third.status == "ready"
    assert compiler.calls == 2
    assert third.metrics.reused_ir == 0
    assert generated == 8


def test_local_pytest_backend_does_not_report_warning_paths_as_failures(tmp_path):
    from benchmark_forge.materialization_workflow import LocalPytestBackend

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_warning.py").write_text(
        "import warnings\n"
        "def test_ok():\n"
        "    warnings.warn('visible warning')\n"
        "    assert True\n",
        encoding="utf-8",
    )
    result = LocalPytestBackend(timeout_seconds=20).run(tmp_path)
    assert result.passed is True
    assert result.failed_test_paths == []
