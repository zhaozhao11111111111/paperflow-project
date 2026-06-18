"""
Module 4 end-to-end demo.

Pipeline:
  module2 Markdown (cover_image empty)
    -> module4 generate cover
    -> module3 render WeChat preview HTML

Run from this folder (API keys in env.bundle, no extra setup):
    pip install -r requirements.txt
    python demo_module4.py

See PACKAGE_README.md for recipients.
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

MODULE4_DIR = Path(__file__).resolve().parent
GET_DATA_DIR = MODULE4_DIR.parent
for path in (MODULE4_DIR, GET_DATA_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cover_generator_core import DEFAULT_SUMMARIES_DIR, generate_cover_for_markdown, run_pipeline
from md_to_wechat_core import DEFAULT_OUTPUT_DIR, list_markdown_files, render_preview_html


def main() -> None:
    summaries_dir = DEFAULT_SUMMARIES_DIR
    files = list_markdown_files(summaries_dir)
    if not files:
        raise SystemExit("No summaries/*.md found. Run module2 first.")

    print("\n" + "=" * 60)
    print("  Module 4 Demo: cover -> WeChat preview")
    print("=" * 60)

    print("\n[Step 1] Markdown files:")
    for i, path in enumerate(files, 1):
        print(f"  {i}. {path.name}")

    target = files[0]
    print(f"\n[Step 2] Generate cover for: {target.name}")
    cover_result = generate_cover_for_markdown(
        target,
        summaries_dir=summaries_dir,
        backend="auto",
        force=True,
    )
    print(json.dumps(cover_result, ensure_ascii=False, indent=2))

    cover_abs = Path(cover_result["cover_abs"])
    if not cover_abs.exists():
        raise SystemExit(f"Cover file missing: {cover_abs}")

    print("\n[Step 3] Module3 preview HTML")
    preview_path = render_preview_html(target, DEFAULT_OUTPUT_DIR)
    print(f"  preview: {preview_path}")

    if len(files) > 1:
        print("\n[Step 4] Batch covers for remaining files")
        batch_results = run_pipeline(
            summaries_dir=summaries_dir,
            backend="auto",
            max_articles=len(files),
            force=True,
        )
        print(f"  done: {len(batch_results)} articles")

    print("\n" + "=" * 60)
    print("  Demo complete")
    print(f"  cover:  {cover_abs}")
    print(f"  markdown: {target}")
    print(f"  preview:  {preview_path}")
    print("=" * 60)

    try:
        webbrowser.open(preview_path.as_uri())
        print("\nOpened preview in browser.")
    except Exception:
        print("\nOpen the preview HTML manually in a browser.")


if __name__ == "__main__":
    main()
