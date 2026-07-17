from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import CommitAnalysis, CommitRecord, Config, FileContext, Risk, Summary


logger = logging.getLogger(__name__)


class CommitLLMOutput(BaseModel):
    """严格校验模型返回的单个提交分析结果。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    summary: str = Field(min_length=1)
    changes: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    risk_level: str = Field(pattern="^(high|medium|low)$")
    risks: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SummaryLLMOutput(BaseModel):
    """严格校验模型返回的总体汇总结果。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    overall: str = Field(min_length=1)
    feature_changes: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    risk_level: str = Field(pattern="^(high|medium|low)$")
    recommendations: list[str] = Field(default_factory=list)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item and item.strip()))


def _json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


class LLMAnalyzer:
    """OpenAI-compatible analyzer with a deterministic local fallback."""

    def __init__(self, config: Config):
        self.config = config
        self.client: Any | None = None
        if config.api_key:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "检测到 API_KEY，但未安装 langchain-openai。请执行: pip install -r requirements.txt"
                ) from exc
            kwargs: dict[str, Any] = {"model": config.model, "api_key": config.api_key, "temperature": 0}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            self.client = ChatOpenAI(**kwargs)

    def _invoke(self, prompt: str) -> str:
        if self.client is None:
            raise RuntimeError("模型客户端未初始化")
        response = self.client.invoke(prompt)
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        return str(content)

    def _request_validated(
        self,
        prompt: str,
        schema: type[CommitLLMOutput] | type[SummaryLLMOutput],
        label: str,
    ) -> CommitLLMOutput | SummaryLLMOutput | None:
        """请求模型并校验结果；网络失败或格式不合规都会按配置重试。"""
        if self.client is None:
            return None

        attempts = self.config.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                raw = self._invoke(prompt)
                data = _json_object(raw)
                if data is None:
                    raise ValueError("模型返回内容不是有效 JSON 对象")
                return schema.model_validate(data)
            except Exception as exc:
                if attempt >= attempts:
                    logger.error("%s失败，已达到最大尝试次数 %d/%d：%s", label, attempt, attempts, exc)
                    return None
                delay = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                logger.warning("%s失败（第 %d/%d 次）：%s；%.1f 秒后重试", label, attempt, attempts, exc, delay)
                time.sleep(delay)
        return None

    def analyze_commit(self, commit: CommitRecord, contexts: list[FileContext]) -> CommitAnalysis:
        file_text = "\n\n".join(
            f"### {item.path} ({item.status})\n{item.content if not item.is_binary else '[二进制文件，已跳过]'}"
            for item in contexts
        )
        prompt = f"""你是资深代码审查工程师。请根据一个 Git 提交的元数据、diff 和变更文件上下文进行分析。
只返回 JSON，不要 Markdown，字段必须为：summary(string), changes(string[]), modules(string[]),
risk_level(one of high/medium/low), risks(string[]), checks(string[]), evidence(string[])。
不要臆测；每个风险都要能在 diff 或文件上下文中找到依据。

提交：{commit.sha} {commit.message}
作者：{commit.author} <{commit.author_email}>，时间：{commit.authored_at}
变更文件：{', '.join(item.path for item in contexts) or '无'}
DIFF：
{commit.diff}
文件上下文：
{file_text}
"""
        parsed = self._request_validated(prompt, CommitLLMOutput, f"提交 {commit.sha[:8]} 模型分析")
        if isinstance(parsed, CommitLLMOutput):
            return CommitAnalysis(
                sha=commit.sha,
                message=commit.message,
                summary=parsed.summary,
                changes=parsed.changes,
                modules=parsed.modules,
                risk_level=parsed.risk_level,
                risks=parsed.risks,
                checks=parsed.checks,
                evidence=parsed.evidence,
            )
        if self.client is not None:
            logger.warning("提交 %s 模型结果无效，已降级为离线基础分析", commit.sha[:8])
        return self._fallback_commit(commit, contexts)

    def _fallback_commit(self, commit: CommitRecord, contexts: list[FileContext]) -> CommitAnalysis:
        paths = [item.path for item in contexts]
        modules = _unique([path.split("/", 1)[0] for path in paths])
        changes: list[str] = []
        for item in contexts:
            action = {"A": "新增", "M": "修改", "D": "删除"}.get(item.status[0], "变更")
            changes.append(f"{action} `{item.path}`")
        return CommitAnalysis(
            sha=commit.sha,
            message=commit.message,
            summary=f"{commit.message}（涉及 {len(paths)} 个文件）",
            changes=changes or ["提交未包含可分析的文本文件"],
            modules=modules or ["未识别模块"],
            risk_level="low",
            checks=["当前未配置模型 API，已生成基于 Git 元数据和文件路径的基础分析"],
        )

    def summarize(self, analyses: list[CommitAnalysis], risks: list[Risk]) -> Summary:
        if not analyses:
            return Summary("指定范围内没有可分析的提交。", [], [], "low", [])
        digest = "\n".join(f"- {item.sha[:8]}: {item.summary}" for item in analyses)
        prompt = f"""请汇总以下 Git 提交分析，只返回 JSON：overall(string), feature_changes(string[]),
modules(string[]), risk_level(high/medium/low), recommendations(string[])。
要求用中文、面向开发者，区分功能改动、配置/依赖/文档和重构，并避免复述所有 commit message。
提交分析：
{digest}
风险：\n""" + "\n".join(f"- [{r.level}] {r.title}: {r.detail}" for r in risks)
        parsed = self._request_validated(prompt, SummaryLLMOutput, "总体汇总模型分析")
        if isinstance(parsed, SummaryLLMOutput):
            return Summary(
                overall=parsed.overall,
                feature_changes=parsed.feature_changes,
                modules=parsed.modules,
                risk_level=parsed.risk_level,
                recommendations=parsed.recommendations,
            )
        if self.client is not None:
            logger.warning("总体汇总模型结果无效，已使用本地规则汇总")
        modules = _unique([module for item in analyses for module in item.modules])
        changes = _unique([change for item in analyses for change in item.changes])
        level = max((r.level for r in risks), key=lambda x: {"low": 0, "medium": 1, "high": 2}.get(x, 0), default="low")
        return Summary(
            overall=f"共分析 {len(analyses)} 个提交，主要涉及 {', '.join(modules[:8]) or '未识别模块'}。",
            feature_changes=changes[:20],
            modules=modules[:20],
            risk_level=level,
            recommendations=["对报告中列出的风险逐项补充测试或人工验证。"] if risks else ["建议结合业务上下文抽查核心链路。"],
        )
