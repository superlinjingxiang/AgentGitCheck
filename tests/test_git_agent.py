from __future__ import annotations

import subprocess
from pathlib import Path

from git_agent.config import load_config
from git_agent.feishu_notifier import build_feishu_card
from git_agent.graph import run_analysis
from git_agent.models import Summary


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("def login(user):\n    return True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add login")
    (repo / "app.py").write_text("def login(user):\n    # TODO validate user\n    return True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "mark validation TODO")
    return repo


def test_graph_generates_report_without_api_key(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = tmp_path / "report.md"
    result = run_analysis({"repo_path": str(repo), "output_path": str(output), "commit_limit": 2, "api_key": ""})

    assert len(result["commits"]) == 2
    assert result["commits"][0].changed_lines > 0
    assert output.is_file()
    text = output.read_text(encoding="utf-8")
    assert "Git 提交分析报告" in text
    assert "## 0. 提交时间线与变更概览" in text
    assert "| 提交时间 | 提交人 | 提交内容 | 文件数 | 代码量 | 可视化 |" in text
    assert "## 7. 重点提交明细" in text
    assert "mark validation TODO" in text
    assert any(r.title == "新增待办标记" for r in result["risks"])


def test_commit_range(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commits = git(repo, "log", "--format=%H").splitlines()
    output = tmp_path / "range.md"
    result = run_analysis({
        "repo_path": str(repo),
        "output_path": str(output),
        "base_commit": commits[1],
        "head_commit": commits[0],
        "api_key": "",
    })
    assert len(result["commits"]) == 1


def test_num_controls_recent_commit_count(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    (tmp_path / ".env").write_text(
        f"REPO_PATH={repo}\nNUM=7\nCOMMIT_LIMIT=1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NUM", raising=False)
    monkeypatch.delenv("COMMIT_LIMIT", raising=False)

    config = load_config()

    assert config.commit_limit == 7


def test_feishu_card_uses_compact_report_sections() -> None:
    summary = Summary(
        overall="共分析 12 个提交，核心链路已完成改造，建议重点回归支付流程。",
        feature_changes=["改造支付链路", "新增订单校验", "补充回归测试", "更新部署配置", "完善文档", "清理遗留配置"],
        modules=["payment", "order", "tests", "deploy", "docs", "legacy", "monitor", "api", "frontend"],
        risk_level="medium",
        recommendations=[],
    )

    card = build_feishu_card(summary)
    content = card["card"]["elements"][0]["text"]["content"]

    assert "总体结论（风险：medium）" in content
    assert "主要功能改动" in content
    assert "涉及模块" in content
    assert "共 6 项" in content
    assert "共 9 项" in content
