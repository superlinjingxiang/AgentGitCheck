from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .models import Config


def _load_dotenv(path: Path) -> None:
    """Load a tiny .env reader without requiring python-dotenv."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"配置项必须是整数: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"配置项必须大于 0: {value!r}")
    return parsed


def _float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"配置项必须是数字: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"配置项不能小于 0: {value!r}")
    return parsed


def load_config(overrides: Mapping[str, object] | None = None) -> Config:
    _load_dotenv(Path.cwd() / ".env")
    # NUM is the user-facing setting for the number of recent commits to
    # evaluate. Keep COMMIT_LIMIT as a backward-compatible alias.
    commit_limit_value = os.getenv("NUM") or os.getenv("COMMIT_LIMIT")
    values: dict[str, object] = {
        "api_key": os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY"),
        "repo_path": os.getenv("REPO_PATH", "."),
        "model": os.getenv("MODEL", "gpt-4.1"),
        "base_url": os.getenv("OPENAI_BASE_URL") or None,
        "commit_limit": _int(commit_limit_value, 30),
        "since": os.getenv("SINCE") or None,
        "until": os.getenv("UNTIL") or None,
        "base_commit": os.getenv("BASE_COMMIT") or None,
        "head_commit": os.getenv("HEAD_COMMIT") or None,
        "output_path": os.getenv("OUTPUT_PATH", "git-agent-report.md"),
        "feishu_webhook_url": os.getenv("FEISHU_WEBHOOK_URL") or None,
        "feishu_enabled": _bool(os.getenv("FEISHU_ENABLED")),
        "max_diff_chars": _int(os.getenv("MAX_DIFF_CHARS"), 30_000),
        "max_file_chars": _int(os.getenv("MAX_FILE_CHARS"), 20_000),
        "max_retries": _int(os.getenv("MAX_RETRIES"), 3),
        "retry_delay_seconds": _float(os.getenv("RETRY_DELAY_SECONDS"), 2.0),
    }
    if overrides:
        values.update({k: v for k, v in overrides.items() if v is not None})

    repo_path = Path(str(values["repo_path"])).expanduser().resolve()
    if not repo_path.is_dir():
        raise ValueError(f"REPO_PATH 不是有效目录: {repo_path}")

    output_path = Path(str(values["output_path"])).expanduser()
    config = Config(
        api_key=str(values["api_key"]) if values.get("api_key") else None,
        repo_path=repo_path,
        model=str(values["model"]),
        base_url=str(values["base_url"]) if values.get("base_url") else None,
        commit_limit=int(values["commit_limit"]),
        since=str(values["since"]) if values.get("since") else None,
        until=str(values["until"]) if values.get("until") else None,
        base_commit=str(values["base_commit"]) if values.get("base_commit") else None,
        head_commit=str(values["head_commit"]) if values.get("head_commit") else None,
        output_path=output_path,
        feishu_webhook_url=(str(values["feishu_webhook_url"]) if values.get("feishu_webhook_url") else None),
        feishu_enabled=bool(values["feishu_enabled"]),
        max_diff_chars=int(values["max_diff_chars"]),
        max_file_chars=int(values["max_file_chars"]),
        max_retries=int(values["max_retries"]),
        retry_delay_seconds=float(values["retry_delay_seconds"]),
    )
    if config.feishu_enabled and not config.feishu_webhook_url:
        raise ValueError("FEISHU_ENABLED=true 时必须配置 FEISHU_WEBHOOK_URL")
    return config
