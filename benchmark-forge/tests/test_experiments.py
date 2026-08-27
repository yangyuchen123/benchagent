from benchmark_forge import Benchmark, UserGoal
from benchmark_forge.domain import BenchmarkDimension, BenchmarkGrounding, BenchmarkItem, GroundingStatus, SourceMode, SourceRef
from benchmark_forge.experiments import compute_metrics, lexical_alignment, structural_difficulty, summarize_runs, ExperimentRun
from benchmark_forge.octagon.profile import EnvironmentDimension, EnvironmentProfile


def fixture_profile(tmp_path):
    return EnvironmentProfile(
        env_id="parallel", name="parallel", env_type="skill", category="agent-system",
        test_focus="parallel scheduling dependency constraints", description="run independent tasks concurrently",
        dimensions=[
            EnvironmentDimension(name="parallelism", weight=50, description="true concurrency"),
            EnvironmentDimension(name="dependency_safety", weight=50, description="respect dependencies"),
        ], source_root=str(tmp_path), meta_path=str(tmp_path / "meta.yaml"), task_paths=["tasks/one.json"],
    )


def fixture_benchmark():
    benchmark = Benchmark(
        benchmark_id="b", user_goal=UserGoal(goal_id="g", description="parallel scheduling dependency constraints", target_size=1),
        dimensions=[
            BenchmarkDimension(id="parallelism", name="Parallelism", description="measure true concurrency", constraints={"trace": True}),
            BenchmarkDimension(id="dependency", name="Dependency safety", description="respect dependency ordering", constraints={"timestamps": True}),
        ],
        groundings=[BenchmarkGrounding(dimension_id="parallelism", source_mode=SourceMode.EXISTING_DATASET, source_id="p", executable_capacity=1, estimated_capacity=1, status=GroundingStatus.READY)],
    )
    benchmark.items.append(BenchmarkItem(
        item_id="i", dimension_id="parallelism", source_mode=SourceMode.EXISTING_DATASET,
        source_id="p", question="Run A and B concurrently, then C", context="timestamps are recorded",
        options=["A/B then C", "C then A/B"], answer="A/B then C",
        source_refs=[SourceRef(source_mode=SourceMode.EXISTING_DATASET, source_id="p", sample_id="1")],
    ))
    return benchmark


def test_experiment_metrics_are_bounded(tmp_path):
    benchmark = fixture_benchmark()
    profile = fixture_profile(tmp_path)
    assert 0 <= lexical_alignment(benchmark, profile) <= 100
    assert 0 <= structural_difficulty(benchmark) <= 100
    metrics = compute_metrics(benchmark, profile, 1.2)
    assert metrics.artifact_valid
    assert metrics.completion_ratio == 1


def test_experiment_summary_compares_arms(tmp_path):
    benchmark = fixture_benchmark()
    metrics = compute_metrics(benchmark, fixture_profile(tmp_path), 1)
    runs = [
        ExperimentRun(case_id="x", arm="without_kb", metrics=metrics),
        ExperimentRun(case_id="x", arm="with_kb", metrics=metrics),
    ]
    summary = summarize_runs(runs)
    assert summary["without_kb"]["n"] == 1
    assert summary["with_kb"]["process_success"] == summary["without_kb"]["process_success"]
