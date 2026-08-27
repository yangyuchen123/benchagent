from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate autonomous materialization workflow reports")
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = []
    historical = sorted(args.root.rglob("workflow-runs/*/report.json"))
    paths = historical or sorted(args.root.rglob("workflow-report.json"))
    for path in paths:
        try:
            reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    statuses = Counter(report.get("status", "unknown") for _, report in reports)
    stage_attempts: Counter[str] = Counter()
    stage_failures: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for _, report in reports:
        for event in report.get("events", []):
            if event.get("status") not in {"reused", "skipped"}:
                stage_attempts[event.get("stage", "unknown")] += 1
            if event.get("status") == "failed":
                stage_failures[event.get("stage", "unknown")] += 1
        totals.update(report.get("metrics", {}))
    count = len(reports)
    manual = sum(bool(report.get("manual_intervention_required")) for _, report in reports)
    ready = statuses.get("ready", 0)
    summary = {
        "schema_version": "benchmark-forge.materialization-aggregate.v1",
        "root": str(args.root),
        "runs": count,
        "statuses": dict(statuses),
        "ready_rate": ready / count if count else None,
        "zero_human_intervention_rate": (count - manual) / count if count else None,
        "manual_intervention_rate": manual / count if count else None,
        "stage_attempts": dict(stage_attempts),
        "stage_failures": dict(stage_failures),
        "stage_failure_rate": {
            stage: stage_failures[stage] / attempts for stage, attempts in stage_attempts.items()
        },
        "metric_totals": dict(totals),
        "reports": [str(path) for path, _ in reports],
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
