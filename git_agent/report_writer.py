from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import CommitAnalysis, CommitRecord, Config, FileContext, Risk, Summary


TIMELINE_LIMIT = 20
KEY_COMMIT_LIMIT = 10
TIMELINE_BAR_WIDTH = 12


def _bullet(items: list[str], empty: str = "暂无") -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"


def _compact(value: str, limit: int = 72) -> str:
    normalized = " ".join(value.replace("|", "\\|").split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit - 1]}…"


def _display_time(value: str) -> str:
    return value.replace("T", " ")[:16]


def _volume_bar(value: int, ceiling: int) -> str:
    if not value or not ceiling:
        return "—"
    filled = max(1, round(value / ceiling * TIMELINE_BAR_WIDTH))
    return "█" * filled + "░" * (TIMELINE_BAR_WIDTH - filled)


def _timeline_section(commits: list[CommitRecord], analyses_by_sha: dict[str, CommitAnalysis]) -> list[str]:
    if not commits:
        return ["## 0. 提交时间线与变更概览", "", "- 指定范围内没有可展示的提交。", ""]

    shown = commits[:TIMELINE_LIMIT]
    max_volume = max((commit.changed_lines for commit in shown), default=0)
    total_additions = sum(commit.additions for commit in commits)
    total_deletions = sum(commit.deletions for commit in commits)
    authors = {commit.author for commit in commits}
    lines = [
        "## 0. 提交时间线与变更概览",
        "",
        f"- 已评估：{len(commits)} 次提交，{len(authors)} 位提交人，新增 {total_additions} 行，删除 {total_deletions} 行。",
        f"- 时间线展示：最近 {len(shown)} 次提交；`█` 越长表示该提交的新增与删除代码量越大。",
        "",
        "| 提交时间 | 提交人 | 提交内容 | 文件数 | 代码量 | 可视化 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for commit in shown:
        analysis = analyses_by_sha.get(commit.sha)
        description = analysis.summary if analysis else commit.message
        volume = f"+{commit.additions} / -{commit.deletions}"
        lines.append(
            "| "
            f"{_display_time(commit.authored_at)} | {_compact(commit.author, 24)} | "
            f"{_compact(description)} | {len(commit.files)} | {volume} | "
            f"{_volume_bar(commit.changed_lines, max_volume)} |"
        )
    if len(commits) > len(shown):
        lines.extend(["", f"> 其余 {len(commits) - len(shown)} 次提交已纳入总体分析，未在时间线逐条展开。"])
    lines.append("")
    return lines


def _key_commit_reason(commit: CommitRecord, analysis: CommitAnalysis) -> str:
    if analysis.risk_level in {"high", "medium"} or analysis.risks:
        return f"风险等级：{analysis.risk_level}"
    return f"变更量较大：{commit.changed_lines} 行"


def _key_commit_entries(
    commits: list[CommitRecord], analyses: list[CommitAnalysis]
) -> list[tuple[CommitRecord, CommitAnalysis]]:
    pairs = list(zip(commits, analyses))
    ranked = sorted(
        pairs,
        key=lambda pair: (
            1 if pair[1].risk_level in {"high", "medium"} or pair[1].risks else 0,
            pair[0].changed_lines,
        ),
        reverse=True,
    )
    return ranked[:KEY_COMMIT_LIMIT]


def _files_summary(commit: CommitRecord, limit: int = 6) -> str:
    paths = [item.path for item in commit.files]
    if not paths:
        return "无可分析文件"
    visible = ", ".join(f"`{path}`" for path in paths[:limit])
    return f"{visible} 等 {len(paths)} 个文件" if len(paths) > limit else visible


def write_report(
    config: Config,
    commits: list[CommitRecord],
    contexts: dict[str, list[FileContext]],
    analyses: list[CommitAnalysis],
    risks: list[Risk],
    summary: Summary,
) -> Path:
    del contexts  # File contexts are consumed by analysis; the report only needs the results.
    output = config.output_path
    output.parent.mkdir(parents=True, exist_ok=True)
    if config.base_commit or config.head_commit:
        scope = f"commit 范围：{config.base_commit or '仓库起点'}..{config.head_commit or 'HEAD'}"
    elif config.since or config.until:
        scope = f"时间范围：{config.since or '起始'} 至 {config.until or '当前'}"
    else:
        scope = f"最近 {config.commit_limit} 次提交"

    analyses_by_sha = {analysis.sha: analysis for analysis in analyses}
    lines = ["# Git 提交分析报告", "", f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}", ""]
    lines.extend(_timeline_section(commits, analyses_by_sha))
    lines.extend([
        "## 1. 分析范围",
        "",
        f"- 仓库：`{config.repo_path}`",
        f"- 范围：{scope}",
        f"- 实际提交数：{len(commits)}",
        "",
        "## 2. 总体结论",
        "",
        summary.overall,
        "",
        f"总体风险：`{summary.risk_level}`",
        "",
        "## 3. 主要功能改动",
        "",
        _bullet(summary.feature_changes),
        "",
        "## 4. 涉及模块",
        "",
        _bullet(summary.modules),
        "",
        "## 5. 潜在 bug 和风险",
        "",
        _bullet([f"[{risk.level}] {risk.title}：{risk.detail}（证据：{risk.evidence}）" for risk in risks]),
        "",
        "## 6. 建议人工重点检查的位置",
        "",
        _bullet(summary.recommendations),
        "",
        "## 7. 重点提交明细",
        "",
    ])

    key_entries = _key_commit_entries(commits, analyses)
    if not key_entries:
        lines.append("- 暂无")
    else:
        lines.append(f"> 从 {len(commits)} 次提交中按风险和变更量筛选，最多展示 {KEY_COMMIT_LIMIT} 条。")
        for index, (commit, analysis) in enumerate(key_entries, 1):
            lines.extend([
                "",
                f"### {index}. `{commit.sha[:12]}` {_compact(commit.message, 96)}",
                "",
                f"- 重点原因：{_key_commit_reason(commit, analysis)}",
                f"- 提交人和时间：{commit.author}，{_display_time(commit.authored_at)}",
                f"- 代码量：新增 {commit.additions} 行，删除 {commit.deletions} 行；涉及 {len(commit.files)} 个文件。",
                f"- 涉及文件：{_files_summary(commit)}",
                f"- 提交结论：{analysis.summary}",
            ])
            if analysis.risks:
                lines.extend(["- 主要风险：", _bullet(analysis.risks)])

    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output.resolve()
