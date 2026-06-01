"""戏台 (Xitai) 评估 CLI.

统一入口，支持三种评估套件:
- swe_bench_lite: 软件工程任务
- humaneval: 代码生成
- custom: 自建多 Agent 评估任务

Usage:
    # 查看帮助
    uv run python -m eval.main --help

    # 跑全部自建任务
    uv run python -m eval.main --suite custom

    # 跑 HumanEval 前 10 题
    uv run python -m eval.main --suite humaneval --limit 10

    # 跑 SWE-bench-lite 前 3 题
    uv run python -m eval.main --suite swe_bench_lite --limit 3

    # 指定配置路径
    uv run python -m eval.main --suite custom --config agent.json

    # 只跑某类任务
    uv run python -m eval.main --suite custom --difficulty easy
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from eval.swe_bench_lite.harness import SWEBenchHarness
from eval.humaneval.harness import HumanEvalHarness
from eval.custom.harness import CustomHarness


SUITES = {
    "swe_bench_lite": SWEBenchHarness,
    "humaneval": HumanEvalHarness,
    "custom": CustomHarness,
}


def main():
    parser = argparse.ArgumentParser(
        description="Xitai Evaluation Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python -m eval.main --suite custom
  uv run python -m eval.main --suite humaneval --limit 10
  uv run python -m eval.main --suite swe_bench_lite --limit 3
        """,
    )
    parser.add_argument("--suite", required=True, choices=list(SUITES.keys()),
                        help="评估套件名称")
    parser.add_argument("--limit", type=int, default=None,
                        help="最多跑多少个任务")
    parser.add_argument("--config", default="agent.json",
                        help="Agent 配置文件路径 (默认: agent.json)")
    parser.add_argument("--work-dir", default=".eval_work",
                        help="工作目录 (默认: .eval_work)")
    parser.add_argument("--output", default=None,
                        help="报告输出路径 (默认: eval_report_<suite>.json)")
    parser.add_argument("--source", default=None,
                        help="数据源路径 (JSONL 或 HuggingFace 数据集)")
    parser.add_argument("--split", default="test",
                        help="数据集 split (默认: test)")
    parser.add_argument("--difficulty", default=None,
                        help="只跑指定难度的任务 (easy/medium/hard)")
    parser.add_argument("--tasks-dir", default=None,
                        help="custom 套件的任务目录 (默认: eval/custom/tasks)")

    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if args.output else Path(f"eval_report_{args.suite}.json")

    harness_cls = SUITES[args.suite]

    # 构建 harness 实例
    harness_kwargs = {
        "work_dir": work_dir,
        "config_path": args.config,
        "limit": args.limit,
    }

    if args.suite in ("swe_bench_lite", "humaneval"):
        harness_kwargs["source"] = args.source
        harness_kwargs["split"] = args.split

    if args.suite == "custom" and args.tasks_dir:
        harness_kwargs["tasks_dir"] = args.tasks_dir

    harness = harness_cls(**harness_kwargs)

    print(f"=" * 60)
    print(f"Xitai Eval: {args.suite}")
    print(f"Config: {args.config}")
    print(f"Work dir: {work_dir}")
    print(f"Limit: {args.limit or 'all'}")
    print(f"=" * 60)

    try:
        results = asyncio.run(harness.run_all())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)

    report = harness.report()
    harness.save_report(output_path)

    # 打印汇总
    print(f"\n{'=' * 60}")
    print(f"RESULTS")
    print(f"{'=' * 60}")
    if "summary" in report:
        s = report["summary"]
        print(f"Total tasks: {s['total']}")
        print(f"Passed:      {s['passed']} ({s['pass_rate']:.1%})")
        print(f"Avg score:   {s['avg_success_score']:.2f}")
        print(f"Avg steps:   {s['avg_steps']:.1f}")
        print(f"Avg tokens:  {s['avg_tokens']:.0f}")
        print(f"Avg time:    {s['avg_duration_sec']:.1f}s")

    if "by_difficulty" in report and report["by_difficulty"]:
        print(f"\nBy difficulty:")
        for diff, data in report["by_difficulty"].items():
            print(f"  {diff:8s}: {data['passed']}/{data['count']} ({data['pass_rate']:.1%})")

    print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()
