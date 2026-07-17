"""Summary facade kept separate so the graph can swap summarization strategies later."""

from __future__ import annotations

from .analyzer import LLMAnalyzer
from .models import CommitAnalysis, Risk, Summary


def summarize_all_commits(analyzer: LLMAnalyzer, analyses: list[CommitAnalysis], risks: list[Risk]) -> Summary:
    return analyzer.summarize(analyses, risks)
