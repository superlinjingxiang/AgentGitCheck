from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Config:
    api_key: str | None
    repo_path: Path
    model: str = "gpt-4.1"
    base_url: str | None = None
    commit_limit: int = 30
    since: str | None = None
    until: str | None = None
    base_commit: str | None = None
    head_commit: str | None = None
    output_path: Path = Path("git-agent-report.md")
    feishu_webhook_url: str | None = None
    feishu_enabled: bool = False
    max_diff_chars: int = 30_000
    max_file_chars: int = 20_000
    max_retries: int = 3
    retry_delay_seconds: float = 2.0
    ignored_patterns: tuple[str, ...] = (
        "*.lock",
        "*.log",
        "*.min.js",
        "*.map",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "dist/*",
        "build/*",
        "coverage/*",
        "*.pyc",
    )


@dataclass(slots=True)
class ChangedFile:
    path: str
    status: str
    old_path: str | None = None
    is_binary: bool = False


@dataclass(slots=True)
class CommitRecord:
    sha: str
    message: str
    author: str
    author_email: str
    authored_at: str
    files: list[ChangedFile] = field(default_factory=list)
    diff: str = ""
    additions: int = 0
    deletions: int = 0

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(slots=True)
class FileContext:
    path: str
    status: str
    content: str = ""
    truncated: bool = False
    is_binary: bool = False


@dataclass(slots=True)
class CommitAnalysis:
    sha: str
    message: str
    summary: str
    changes: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    risk_level: str = "low"
    risks: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Risk:
    level: str
    title: str
    detail: str
    evidence: str = ""


@dataclass(slots=True)
class Summary:
    overall: str
    feature_changes: list[str]
    modules: list[str]
    risk_level: str
    recommendations: list[str]


def analysis_to_dict(value: CommitAnalysis | Risk | Summary) -> dict[str, Any]:
    """Small serializer used by report and notification code."""
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(value)
    raise TypeError(f"Unsupported value: {type(value)!r}")
