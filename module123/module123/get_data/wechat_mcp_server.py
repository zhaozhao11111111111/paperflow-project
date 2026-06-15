"""
模块3 MCP Server：向 MCP Client 暴露公众号排版、预览、草稿、发布工具。

运行前需要安装 MCP Python SDK：
    pip install mcp

启动：
    python wechat_mcp_server.py
"""

from __future__ import annotations

import json
from pathlib import Path

from md_to_wechat_core import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SUMMARIES_DIR,
    batch_upload_latest,
    list_markdown_files,
    render_preview_html,
    upload_markdown_to_draft,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "未安装 MCP Python SDK。请先运行: pip install mcp\n"
        "命令行入口: python md_to_wechat.py preview <markdown>"
    ) from exc


mcp = FastMCP("markdown-to-wechat")


@mcp.tool()
def list_wechat_markdown(summaries_dir: str = str(DEFAULT_SUMMARIES_DIR)) -> str:
    """列出模块2生成、可供公众号发布的 Markdown 文件。"""
    files = list_markdown_files(summaries_dir)
    return json.dumps([str(p) for p in files], ensure_ascii=False, indent=2)


@mcp.tool()
def preview_wechat_article(markdown_path: str, output_dir: str = str(DEFAULT_OUTPUT_DIR)) -> str:
    """把 Markdown 转换为本地公众号预览 HTML，不调用微信接口。"""
    preview = render_preview_html(markdown_path, output_dir)
    return json.dumps(
        {"status": "preview_generated", "preview_html": str(preview)},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def create_wechat_draft(
    markdown_path: str,
    thumb_media_id: str = "",
    dry_run: bool = True,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
) -> str:
    """把 Markdown 上传为微信公众号草稿。dry_run=True 时只生成预览。"""
    result = upload_markdown_to_draft(
        Path(markdown_path),
        thumb_media_id=thumb_media_id or None,
        dry_run=dry_run,
        publish=False,
        output_dir=output_dir,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def publish_wechat_article(
    markdown_path: str,
    thumb_media_id: str = "",
    dry_run: bool = True,
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
) -> str:
    """创建草稿并提交发布。dry_run=True 时只生成预览，不会真正发布。"""
    result = upload_markdown_to_draft(
        Path(markdown_path),
        thumb_media_id=thumb_media_id or None,
        dry_run=dry_run,
        publish=True,
        output_dir=output_dir,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def publish_latest_wechat_articles(
    max_articles: int = 1,
    dry_run: bool = True,
    publish: bool = False,
    summaries_dir: str = str(DEFAULT_SUMMARIES_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
) -> str:
    """处理最新 N 篇 Markdown，可用于模块5流水线编排。"""
    results = batch_upload_latest(
        summaries_dir=summaries_dir,
        max_articles=max_articles,
        dry_run=dry_run,
        publish=publish,
        output_dir=output_dir,
    )
    return json.dumps(results, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
