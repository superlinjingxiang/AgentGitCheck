from __future__ import annotations

import logging
from typing import Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised only in minimal environments
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment,misc]

from .analyzer import LLMAnalyzer
from .config import load_config
from .feishu_notifier import send_feishu_summary
from .git_reader import GitReader
from .models import CommitAnalysis, CommitRecord, Config, FileContext, Risk, Summary
from .report_writer import write_report
from .risk_checker import detect_risks
from .summarizer import summarize_all_commits


logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    overrides: dict[str, object]
    config: Config
    reader: GitReader
    analyzer: LLMAnalyzer
    commits: list[CommitRecord]
    contexts: dict[str, list[FileContext]]
    analyses: list[CommitAnalysis]
    risks: list[Risk]
    summary: Summary
    report_path: str
    notification_sent: bool


class _FallbackGraph:
    """Run the same nodes sequentially when optional LangGraph is unavailable."""

    def invoke(self, state: AgentState) -> AgentState:
        current = dict(state)
        for node in (
            load_config_node,
            read_git_commits_node,
            collect_diffs_node,
            read_related_files_node,
            analyze_commit_changes_node,
            analyze_risks_node,
            summarize_all_commits_node,
            write_markdown_report_node,
            send_feishu_message_node,
        ):
            current.update(node(current))
        return current  # type: ignore[return-value]


def load_config_node(state: AgentState) -> dict[str, Any]:
    logger.info("[1/9] 读取配置文件")
    config = load_config(state.get("overrides", {}))
    mode = "模型分析" if config.api_key else "离线基础模式"
    logger.info("[1/9] 目标仓库：%s；最近提交数：%d；运行模式：%s", config.repo_path, config.commit_limit, mode)
    return {"config": config}


def read_git_commits_node(state: AgentState) -> dict[str, Any]:
    logger.info("[2/9] 检查 Git 仓库并读取提交")
    reader = GitReader(state["config"])
    commits = reader.list_commits()
    logger.info("[2/9] 已读取 %d 个提交", len(commits))
    return {"reader": reader, "commits": commits}


def collect_diffs_node(state: AgentState) -> dict[str, Any]:
    # GitReader already keeps each commit's diff bounded. This node is explicit
    # so a later implementation can batch files or add diff caching.
    logger.info("[3/9] 准备提交 diff（共 %d 个提交）", len(state["commits"]))
    return {"commits": state["commits"]}


def read_related_files_node(state: AgentState) -> dict[str, Any]:
    logger.info("[4/9] 读取提交涉及的文件内容")
    reader = state["reader"]
    contexts: dict[str, list[FileContext]] = {}
    total = len(state["commits"])
    for index, commit in enumerate(state["commits"], 1):
        contexts[commit.sha] = reader.read_related_files(commit)
        logger.info("[4/9] 文件读取进度：%d/%d（提交 %s，文件 %d 个）", index, total, commit.sha[:8], len(contexts[commit.sha]))
    return {"contexts": contexts}


def analyze_commit_changes_node(state: AgentState) -> dict[str, Any]:
    analyzer = LLMAnalyzer(state["config"])
    mode = "模型" if analyzer.client is not None else "离线基础规则"
    logger.info("[5/9] 开始分析提交（模式：%s）", mode)
    analyses: list[CommitAnalysis] = []
    total = len(state["commits"])
    for index, commit in enumerate(state["commits"], 1):
        logger.info("[5/9] 提交分析进度：%d/%d（%s）", index, total, commit.sha[:8])
        analyses.append(analyzer.analyze_commit(commit, state["contexts"].get(commit.sha, [])))
    logger.info("[5/9] 提交分析完成")
    return {"analyzer": analyzer, "analyses": analyses}


def analyze_risks_node(state: AgentState) -> dict[str, Any]:
    logger.info("[6/9] 执行风险启发式检查")
    risks = detect_risks(state["commits"], state["contexts"], state["analyses"])
    logger.info("[6/9] 发现风险项：%d 个", len(risks))
    return {"risks": risks}


def summarize_all_commits_node(state: AgentState) -> dict[str, Any]:
    logger.info("[7/9] 汇总提交分析结果")
    summary = summarize_all_commits(state["analyzer"], state["analyses"], state["risks"])
    logger.info("[7/9] 汇总完成，总体风险：%s", summary.risk_level)
    return {"summary": summary}


def write_markdown_report_node(state: AgentState) -> dict[str, Any]:
    logger.info("[8/9] 写入 Markdown 报告")
    path = write_report(
        state["config"],
        state["commits"],
        state["contexts"],
        state["analyses"],
        state["risks"],
        state["summary"],
    )
    logger.info("[8/9] 报告已写入：%s", path)
    return {"report_path": str(path)}


def send_feishu_message_node(state: AgentState) -> dict[str, Any]:
    logger.info("[9/9] 处理飞书通知：%s", "已启用" if state["config"].feishu_enabled else "未启用，跳过")
    send_feishu_summary(state["config"], state["summary"], state["risks"], state["report_path"])
    return {"notification_sent": bool(state["config"].feishu_enabled)}


def build_graph():
    if StateGraph is None:
        return _FallbackGraph()
    graph = StateGraph(AgentState)
    graph.add_node("load_config", load_config_node)
    graph.add_node("read_git_commits", read_git_commits_node)
    graph.add_node("collect_diffs", collect_diffs_node)
    graph.add_node("read_related_files", read_related_files_node)
    graph.add_node("analyze_commit_changes", analyze_commit_changes_node)
    graph.add_node("analyze_risks", analyze_risks_node)
    graph.add_node("summarize_all_commits", summarize_all_commits_node)
    graph.add_node("write_markdown_report", write_markdown_report_node)
    graph.add_node("send_feishu_message", send_feishu_message_node)
    graph.add_edge(START, "load_config")
    graph.add_edge("load_config", "read_git_commits")
    graph.add_edge("read_git_commits", "collect_diffs")
    graph.add_edge("collect_diffs", "read_related_files")
    graph.add_edge("read_related_files", "analyze_commit_changes")
    graph.add_edge("analyze_commit_changes", "analyze_risks")
    graph.add_edge("analyze_risks", "summarize_all_commits")
    graph.add_edge("summarize_all_commits", "write_markdown_report")
    graph.add_edge("write_markdown_report", "send_feishu_message")
    graph.add_edge("send_feishu_message", END)
    return graph.compile()


def run_analysis(overrides: dict[str, object] | None = None) -> AgentState:
    return build_graph().invoke({"overrides": overrides or {}})
