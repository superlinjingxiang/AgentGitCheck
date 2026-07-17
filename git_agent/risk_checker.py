from __future__ import annotations

import re

from .models import CommitAnalysis, CommitRecord, FileContext, Risk


def _level_max(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def detect_risks(commits: list[CommitRecord], contexts: dict[str, list[FileContext]], analyses: list[CommitAnalysis]) -> list[Risk]:
    risks: list[Risk] = []
    for commit, analysis in zip(commits, analyses):
        for text in analysis.risks:
            risks.append(Risk(analysis.risk_level, "模型识别的风险", text, commit.sha[:8]))
        diff = commit.diff
        if re.search(r"^\+\s*except\s*(?:Exception)?\s*:\s*$", diff, re.MULTILINE) and re.search(r"^\+\s*pass\s*$", diff, re.MULTILINE):
            risks.append(Risk("medium", "异常被静默处理", "新增了捕获异常后直接 pass 的逻辑，可能隐藏真实故障。", commit.sha[:8]))
        if re.search(r"^\+.*(?:TODO|FIXME|XXX)", diff, re.MULTILINE | re.IGNORECASE):
            risks.append(Risk("low", "新增待办标记", "变更中包含 TODO/FIXME/XXX，建议确认是否会进入生产路径。", commit.sha[:8]))
        if any(item.status.startswith("D") for item in commit.files) and re.search(r"(?:def |class |function |export )", diff):
            risks.append(Risk("medium", "删除代码需检查调用方", "提交删除了代码定义，建议搜索仓库中是否仍存在旧引用。", commit.sha[:8]))
        if any(item.status.startswith("M") for item in commit.files) and re.search(r"(?:schema|migration|models?|api|route|config)", diff, re.IGNORECASE):
            risks.append(Risk("low", "核心接口或配置发生变化", "变更可能影响外部调用方或部署环境，建议检查兼容性和配置样例。", commit.sha[:8]))
    dedup: dict[tuple[str, str], Risk] = {}
    for risk in risks:
        key = (risk.title, risk.detail)
        if key not in dedup or _level_max(dedup[key].level, risk.level) == risk.level:
            dedup[key] = risk
    return list(dedup.values())
