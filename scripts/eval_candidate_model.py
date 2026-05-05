#!/usr/bin/env python3
"""Evaluate text/scoring dataset quality metrics for a split."""

import argparse

from amg.learning.eval_harness import evaluate_text_dataset, format_text_eval
from amg.learning.scoring_training_export import evaluate_scoring_dataset, format_scoring_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate dataset quality")
    parser.add_argument("--task", type=str, default="text", choices=["text", "scoring"])
    parser.add_argument("--dataset-name", type=str, default="scoring_selection_v1")
    parser.add_argument("--split", type=str, default="val")
    args = parser.parse_args()

    if args.task == "scoring":
        metrics = evaluate_scoring_dataset(dataset_name=args.dataset_name, split=args.split)
        print(format_scoring_eval(metrics))
    else:
        metrics = evaluate_text_dataset(dataset_name=args.dataset_name, split=args.split)
        print(format_text_eval(metrics))
    return 0 if metrics.get("total_rows", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
