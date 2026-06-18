"""
模块2 - 调用入口：论文 → 结构化 Markdown 总结
运行后交互式输入参数，直接回车使用默认值
"""

from paper_summarizer_core import run_pipeline, summarize_paper, DEFAULT_PAPERS_DIR, DEFAULT_OUTPUT_DIR

DEFAULTS = {
    "max_papers": 5,
    "use_pdf": True,
}


def _input(prompt: str, default):
    raw = input(f"  {prompt} (默认: {default}): ").strip()
    if not raw:
        return default
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            print(f"    输入无效，使用默认值 {default}")
            return default
    if isinstance(default, bool):
        return raw.lower() in ("y", "yes", "true", "1", "是", "开启", "开")
    return raw


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  论文 → Markdown 结构化总结 (模块2)")
    print("  读取 papers/ 下 JSON，生成 summaries/ 下 Markdown")
    print("  直接回车使用默认值")
    print("=" * 50 + "\n")

    max_papers = _input("单次最多处理篇数", DEFAULTS["max_papers"])
    use_pdf = _input("是否下载 PDF 提取封面/配图 (y/n)", DEFAULTS["use_pdf"])
    force_raw = input("  是否强制重新总结已处理论文 (y/n，默认 n): ").strip().lower()
    force = force_raw in ("y", "yes", "true", "1", "是")

    single = input("  指定单篇 JSON 路径 (可选，直接回车批量处理): ").strip()

    print()

    if single:
        summarize_paper(single, output_dir=DEFAULT_OUTPUT_DIR, use_pdf=use_pdf, force=force)
    else:
        run_pipeline(
            papers_dir=DEFAULT_PAPERS_DIR,
            output_dir=DEFAULT_OUTPUT_DIR,
            use_pdf=use_pdf,
            max_papers=max_papers,
            force=force,
        )
