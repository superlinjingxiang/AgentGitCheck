from __future__ import annotations

import argparse
import logging
import sys

from git_agent.graph import run_analysis


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析本地 Git 提交并生成 Markdown 报告")
    parser.add_argument("--repo", dest="repo_path", help="本地 Git 仓库路径，默认读取 REPO_PATH")
    parser.add_argument("--limit", dest="commit_limit", type=int, help="分析最近多少个提交")
    parser.add_argument("--since", help="Git since 日期，例如 2026-07-01")
    parser.add_argument("--until", help="Git until 日期，例如 2026-07-17")
    parser.add_argument("--base", dest="base_commit", help="范围起点 commit")
    parser.add_argument("--head", dest="head_commit", help="范围终点 commit，默认 HEAD")
    parser.add_argument("--output", dest="output_path", help="Markdown 报告路径")
    return parser


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    args = build_parser().parse_args()
    overrides = {key: value for key, value in vars(args).items() if value is not None}
    logger.info("开始 Git 提交影响评估")
    try:
        result = run_analysis(overrides)
    except Exception as exc:
        logger.exception("分析失败：%s", exc)
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    logger.info("分析完成：报告=%s，提交数=%d，总体风险=%s", result["report_path"], len(result["commits"]), result["summary"].risk_level)
    print(f"分析完成：{result['report_path']}")
    print(f"提交数：{len(result['commits'])}，总体风险：{result['summary'].risk_level}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
