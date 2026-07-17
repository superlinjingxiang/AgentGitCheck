from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

from .models import ChangedFile, CommitRecord, Config, FileContext


class GitReaderError(RuntimeError):
    pass


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n\n[内容已截断，原始长度 {len(text)} 字符]", True


class GitReader:
    def __init__(self, config: Config):
        self.config = config
        self.git_executable = os.getenv("GIT_EXECUTABLE") or shutil.which("git") or "git"
        self.repo_path = self._discover_root(config.repo_path)

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            [self.git_executable, *args],
            cwd=self.repo_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitReaderError(f"git {' '.join(args)} 执行失败: {detail}")
        return result.stdout

    def _discover_root(self, path: Path) -> Path:
        result = subprocess.run(
            [self.git_executable, "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitReaderError(f"目录不是 Git 仓库: {path}")
        return Path(result.stdout.strip()).resolve()

    def _matches_ignored(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        return any(
            fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern)
            for pattern in self.config.ignored_patterns
        )

    def _range_args(self) -> list[str]:
        base, head = self.config.base_commit, self.config.head_commit
        if base and head:
            return [f"{base}..{head}"]
        if base:
            return [f"{base}..HEAD"]
        if head:
            return [head]
        return []

    def list_commits(self) -> list[CommitRecord]:
        # 新初始化但尚未提交的仓库没有 HEAD，返回空结果并让报告节点生成说明。
        if not self._git("rev-parse", "--verify", "HEAD", check=False).strip():
            return []
        args = ["log", "--format=%H", "--date-order"]
        range_args = self._range_args()
        if not range_args:
            args.extend(["-n", str(self.config.commit_limit)])
        if self.config.since:
            args.append(f"--since={self.config.since}")
        if self.config.until:
            args.append(f"--until={self.config.until}")
        args.extend(range_args)
        shas = [line.strip() for line in self._git(*args).splitlines() if line.strip()]
        if range_args and len(shas) > self.config.commit_limit:
            shas = shas[: self.config.commit_limit]
        return [self._read_commit(sha) for sha in shas]

    def _read_commit(self, sha: str) -> CommitRecord:
        metadata = self._git(
            "show", "-s", "--format=%H%x1f%s%x1f%an%x1f%ae%x1f%aI", sha
        ).strip("\n")
        parts = metadata.split("\x1f", 4)
        if len(parts) != 5:
            raise GitReaderError(f"无法解析提交元数据: {sha}")
        raw_files = self._git(
            "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", sha
        )
        files: list[ChangedFile] = []
        for line in raw_files.splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            status = fields[0]
            if status.startswith(("R", "C")) and len(fields) >= 3:
                path, old_path = fields[2], fields[1]
            else:
                path, old_path = fields[-1], None
            if not self._matches_ignored(path):
                files.append(ChangedFile(path=path, status=status, old_path=old_path))
        diff = self._git("show", "--format=", "--no-ext-diff", "--binary", "-M", sha, "--")
        diff, _ = _truncate(diff, self.config.max_diff_chars)
        additions, deletions = self._read_numstat(sha)
        return CommitRecord(
            sha=parts[0],
            message=parts[1],
            author=parts[2],
            author_email=parts[3],
            authored_at=parts[4],
            files=files,
            diff=diff,
            additions=additions,
            deletions=deletions,
        )

    def _read_numstat(self, sha: str) -> tuple[int, int]:
        """Read per-commit line additions and deletions, excluding binary-file sizes."""
        additions = 0
        deletions = 0
        raw_numstat = self._git("show", "--format=", "--numstat", "-M", sha, "--")
        for line in raw_numstat.splitlines():
            fields = line.split("\t", 2)
            if len(fields) < 3:
                continue
            if self._matches_ignored(fields[2]):
                continue
            try:
                additions += int(fields[0])
                deletions += int(fields[1])
            except ValueError:
                # Git uses '-' for binary-file entries; they have no meaningful line count.
                continue
        return additions, deletions

    def read_related_files(self, commit: CommitRecord) -> list[FileContext]:
        contexts: list[FileContext] = []
        for changed in commit.files:
            if changed.status.startswith("D"):
                contexts.append(FileContext(changed.path, changed.status))
                continue
            result = subprocess.run(
                [self.git_executable, "show", f"{commit.sha}:{changed.path}"],
                cwd=self.repo_path,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                path = self.repo_path / changed.path
                if path.is_file():
                    raw = path.read_bytes()
                else:
                    raw = b""
            else:
                raw = result.stdout
            if b"\x00" in raw:
                contexts.append(FileContext(changed.path, changed.status, is_binary=True))
                changed.is_binary = True
                continue
            content, truncated = _truncate(raw.decode("utf-8", errors="replace"), self.config.max_file_chars)
            contexts.append(FileContext(changed.path, changed.status, content, truncated))
        return contexts
