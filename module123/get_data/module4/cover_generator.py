"""
Module 4 CLI: generate WeChat article cover images.

Usage:
    python cover_generator.py list
    python cover_generator.py one ../summaries/xxx.md
    python cover_generator.py batch --max-articles 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE4_DIR = Path(__file__).resolve().parent
if str(MODULE4_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE4_DIR))

from cover_generator_core import (  # noqa: E402
    DEFAULT_SUMMARIES_DIR,
    generate_cover_for_markdown,
    run_pipeline,
)

GET_DATA_DIR = MODULE4_DIR.parent
if str(GET_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(GET_DATA_DIR))

from md_to_wechat_core import list_markdown_files  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Module 4: generate cover images for module2 Markdown articles."
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List markdown files waiting for covers")

    one = sub.add_parser("one", help="Generate cover for one markdown file")
    one.add_argument("markdown", help="Path to markdown file")
    one.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "local", "siliconflow", "pollinations", "dashscope"],
        help="auto=SiliconFlow if key set, else pollinations, fallback local",
    )
    one.add_argument("--force", action="store_true", help="Regenerate even if cover exists")
    one.add_argument(
        "--pure",
        action="store_true",
        help="AI background only, skip programmatic text overlay",
    )
    one.add_argument(
        "--text-only",
        action="store_true",
        help="Re-apply text overlay on saved cover_bg.png (no image API call)",
    )
    one.add_argument("--summaries-dir", default=str(DEFAULT_SUMMARIES_DIR))

    batch = sub.add_parser("batch", help="Batch generate covers")
    batch.add_argument("--max-articles", type=int, default=10)
    batch.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "local", "siliconflow", "pollinations", "dashscope"],
    )
    batch.add_argument("--force", action="store_true")
    batch.add_argument("--pure", action="store_true")
    batch.add_argument("--summaries-dir", default=str(DEFAULT_SUMMARIES_DIR))
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
            print("No markdown files found.")
            return
        for i, path in enumerate(files, 1):
            print(f"{i}. {path}")
        return

    if args.command == "one":
        result = generate_cover_for_markdown(
            args.markdown,
            summaries_dir=args.summaries_dir,
            backend=args.backend,
            force=args.force,
            overlay_text=not args.pure,
            text_only=args.text_only,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "batch":
        results = run_pipeline(
            summaries_dir=args.summaries_dir,
            backend=args.backend,
            max_articles=args.max_articles,
            force=args.force,
            overlay_text=not args.pure,
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
