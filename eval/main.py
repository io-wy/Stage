"""戏台 (Xitai) 评估 CLI.

统一入口，支持三种评估套件:
- swe_bench_lite: 软件工程任务
- humaneval: 代码生成
- custom: 自建多 Agent 评估任务（默认启用 Agent-as-Judge）

Usage:
    # 查看帮助
    uv run python -m eval.main --help

    # 跑全部自建任务（启用 Claude Code CLI Judge）
    uv run python -m eval.main --suite custom

    # 跳过 Judge（只用客观指标）
    uv run python -m eval.main --suite custom --skip-judge

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

from eval.custom.harness import CustomHarness
from eval.humaneval.harness import HumanEvalHarness
from eval.swe_bench_lite.harness import SWEBenchHarness

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
  uv run python -m eval.main --suite custom --skip-judge
  uv run python -m eval.main --suite humaneval --limit 10
  uv run python -m eval.main --suite swe_bench_lite --limit 3
        """,
    )
    parser.add_argument(
        "--suite", required=True, choices=list(SUITES.keys()), help="评估套件名称"
    )
    parser.add_argument("--limit", type=int, default=None, help="最多跑多少个任务")
    parser.add_argument(
        "--config", default="agent.json", help="Agent 配置文件路径 (默认: agent.json)"
    )
    parser.add_argument(
        "--work-dir", default=".eval_work", help="工作目录 (默认: .eval_work)"
    )
    parser.add_argument(
        "--output", default=None, help="报告输出路径 (默认: eval_report_<suite>.json)"
    )
    parser.add_argument(
        "--source", default=None, help="数据源路径 (JSONL 或 HuggingFace 数据集)"
    )
    parser.add_argument("--split", default="test", help="数据集 split (默认: test)")
    parser.add_argument(
        "--difficulty", default=None, help="只跑指定难度的任务 (easy/medium/hard)"
    )
    parser.add_argument(
        "--tasks-dir",
        default=None,
        help="custom 套件的任务目录 (默认: eval/custom/tasks)",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="跳过 Agent-as-Judge（只用客观指标，快但缺少主观评估）",
    )

    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    output_path = (
        Path(args.output) if args.output else Path(f"eval_report_{args.suite}.json")
    )

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

    if args.suite == "custom":
        harness_kwargs["skip_judge"] = args.skip_judge
        if args.tasks_dir:
            harness_kwargs["tasks_dir"] = args.tasks_dir

    harness = harness_cls(**harness_kwargs)

    print("=" * 60)
    print(f"Xitai Eval: {args.suite}")
    print(f"Config: {args.config}")
    print(f"Work dir: {work_dir}")
    print(f"Limit: {args.limit or 'all'}")
    if args.difficulty:
        print(f"Difficulty: {args.difficulty}")
    if args.suite == "custom":
        print(f"Judge: {'skipped' if args.skip_judge else 'enabled (Claude Code CLI)'}")
    print("=" * 60)

    try:
        asyncio.run(harness.run_all(difficulty_filter=args.difficulty))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(1)

    report = harness.report()
    harness.save_report(output_path)

    # 打印汇总
    print(f"\n{'=' * 60}")
    print("RESULTS")
    print(f"{'=' * 60}")
    if "summary" in report:
        s = report["summary"]
        print(f"Total tasks:  {s['total']}")
        print(f"Passed:       {s['passed']} ({s['pass_rate']:.1%})")
        print(f"Task success: {s['avg_task_success']:.2f}")
        print(f"Token eff:    {s['avg_token_efficiency']:.2f}")
        print(f"Orchestration:{s['avg_orchestration_quality']:.2f}")
        print(f"Collaboration:{s['avg_collaboration_success']:.2f}")
        print(f"Recovery:     {s['avg_recovery_rate']:.2f}")
        print(f"Output qual:  {s['avg_output_quality']:.2f}")
        print(f"Autonomy:     {s['avg_autonomy']:.2f}")
        print(f"Avg steps:    {s['avg_steps']:.1f}")
        print(f"Avg tokens:   {s['avg_tokens']:.0f}")
        print(f"Avg time:     {s['avg_duration_sec']:.1f}s")
        if s.get("judge_errors"):
            print(f"Judge errors: {s['judge_errors']}")

    if "by_difficulty" in report and report["by_difficulty"]:
        print("\nBy difficulty:")
        for diff, data in report["by_difficulty"].items():
            print(
                f"  {diff:8s}: {data['passed']}/{data['count']} ({data['pass_rate']:.1%})"
            )

    print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()
