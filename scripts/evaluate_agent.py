#!/usr/bin/env python3
"""Run local Agent regression; --live-model-config explicitly enables paid model calls."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent.regression import compare_experiments, evaluate_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="tests/fixtures/agent_regression.json")
    parser.add_argument("--prompt-file")
    parser.add_argument("--live-model-config", help="Local model config JSON; never copied into reports")
    parser.add_argument("--baseline", help="Previous report using the same dataset")
    parser.add_argument("--output", help="Write a local JSON report")
    args = parser.parse_args()
    report = asyncio.run(evaluate_suite(
        json.loads(Path(args.dataset).read_text()),
        system_prompt=Path(args.prompt_file).read_text() if args.prompt_file else "",
        live_model_config=json.loads(Path(args.live_model_config).read_text()) if args.live_model_config else None,
    ))
    report["git_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    report["working_tree_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    if args.baseline:
        report["changes"] = compare_experiments(report, json.loads(Path(args.baseline).read_text()))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
