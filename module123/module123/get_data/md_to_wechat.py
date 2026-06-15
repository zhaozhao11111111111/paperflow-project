"""
模块3命令行入口：Markdown -> 微信公众号草稿/发布。

默认 dry-run，只生成本地预览 HTML，不会触发微信接口。
"""

from __future__ import annotations

import argparse
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="模块3：将模块2生成的 Markdown 转换为微信公众号图文草稿，并可自动发布。"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出 summaries/ 下可发布的 Markdown 文件")

    preview = sub.add_parser("preview", help="生成本地公众号排版预览 HTML")
    preview.add_argument("markdown", help="模块2生成的 Markdown 文件路径")
    preview.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="预览输出目录")

    draft = sub.add_parser("draft", help="上传为微信公众号草稿")
    draft.add_argument("markdown", help="模块2生成的 Markdown 文件路径")
    draft.add_argument("--thumb-media-id", default="", help="封面素材 media_id")
    draft.add_argument("--publish", action="store_true", help="草稿创建成功后提交发布")
    draft.add_argument(
        "--real",
        action="store_true",
        help="真实调用微信接口。未指定时为 dry-run，只生成预览。",
    )
    draft.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")

    latest = sub.add_parser("latest", help="处理最新的 N 篇 Markdown")
    latest.add_argument("--max-articles", type=int, default=1, help="处理篇数")
    latest.add_argument("--publish", action="store_true", help="提交发布")
    latest.add_argument("--real", action="store_true", help="真实调用微信接口")
    latest.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    latest.add_argument("--summaries-dir", default=str(DEFAULT_SUMMARIES_DIR), help="Markdown目录")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "list":
        files = list_markdown_files(DEFAULT_SUMMARIES_DIR)
        if not files:
            print("未找到可发布 Markdown。")
            return
        for i, path in enumerate(files, 1):
            print(f"{i}. {path}")
        return

    if args.command == "preview":
        preview_path = render_preview_html(args.markdown, args.output_dir)
        print(f"预览已生成: {preview_path}")
        return

    if args.command == "draft":
        result = upload_markdown_to_draft(
            args.markdown,
            thumb_media_id=args.thumb_media_id or None,
            dry_run=not args.real,
            publish=args.publish,
            output_dir=args.output_dir,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "latest":
        results = batch_upload_latest(
            summaries_dir=args.summaries_dir,
            max_articles=args.max_articles,
            dry_run=not args.real,
            publish=args.publish,
            output_dir=args.output_dir,
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
