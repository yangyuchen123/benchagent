"""Command-line entry point.

Usage:
    benchagent --query user_query.json \
               --model-config config/models.yaml \
               --dataset-config config/datasets.yaml \
               [--data-root data/] [--cache-path cache/] [--sample-cap N] [--seed 0]
"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchagent", description=__doc__)
    parser.add_argument("--query", required=True, help="Path to user query JSON")
    parser.add_argument("--model-config", default="config/models.yaml")
    parser.add_argument("--dataset-config", default="config/datasets.yaml")
    parser.add_argument("--data-root", default=None, help="Root dir of raw dataset data")
    parser.add_argument("--cache-path", default="cache")
    parser.add_argument("--sample-cap", type=int, default=None,
                        help="Cap raw samples used per dataset (for quick smoke tests)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallelism for grounding and sample execution")
    parser.add_argument("--model", default=None,
                        help="Override the model for ALL agents/tools (CLI wins over .env and models.yaml)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    from .pipeline import run_pipeline

    samples = run_pipeline(
        query_path=args.query,
        model_config_path=args.model_config,
        dataset_config_path=args.dataset_config,
        data_root=args.data_root,
        cache_path=args.cache_path,
        seed=args.seed,
        sample_cap=args.sample_cap,
        workers=args.workers,
        model=args.model,
    )
    if samples is None:
        print("Pipeline failed — see logs.", file=sys.stderr)
        return 1
    print(f"Generated {len(samples)} verified benchmark samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
