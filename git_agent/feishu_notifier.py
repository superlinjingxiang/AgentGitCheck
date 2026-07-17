from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Config, Risk, Summary


logger = logging.getLogger(__name__)


def _compact_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    boundaries = [normalized.rfind(mark, 0, limit) for mark in "。！？；"]
    end = max(boundaries)
    return normalized[: end + 1] if end >= max(20, limit // 2) else f"{normalized[:limit - 1]}…"


def _compact_items(items: list[str], limit: int, item_limit: int = 70) -> str:
    if not items:
        return "暂无"
    displayed = [_compact_text(item, item_limit) for item in items[:limit]]
    suffix = f"（共 {len(items)} 项）" if len(items) > limit else ""
    return "；".join(displayed) + suffix


def build_feishu_card(summary: Summary) -> dict[str, object]:
    """Build a short card from report sections 2, 3 and 4."""
    content = "\n".join([
        f"**总体结论（风险：{summary.risk_level}）**\n{_compact_text(summary.overall, 220) or '暂无'}",
        f"**主要功能改动**\n{_compact_items(summary.feature_changes, 5)}",
        f"**涉及模块**\n{_compact_items(summary.modules, 8, 42)}",
    ])
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Git 提交分析完成"},
                "template": "blue",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": "内容来自报告第 2、3、4 节，已压缩展示。"}],
                },
            ],
        },
    }


def send_feishu_summary(config: Config, summary: Summary, risks: list[Risk], report_path: str) -> None:
    if not config.feishu_enabled or not config.feishu_webhook_url:
        return
    del risks, report_path
    request = Request(
        config.feishu_webhook_url,
        data=json.dumps(build_feishu_card(summary), ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"HTTP {response.status}")
            raw = response.read()
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"发送飞书报告摘要失败: {exc}") from exc
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("发送飞书报告摘要失败：返回了无效 JSON") from exc
        if isinstance(payload, dict) and payload.get("code", payload.get("StatusCode", 0)) not in (0, "0", None):
            detail = payload.get("msg") or payload.get("StatusMessage") or payload
            raise RuntimeError(f"发送飞书报告摘要失败: {detail}")
    logger.info("飞书报告摘要已发送")
