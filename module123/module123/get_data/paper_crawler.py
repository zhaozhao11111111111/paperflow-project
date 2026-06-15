"""
模块1 - 调用入口：论文爬取与智能筛选
运行后交互式输入参数，直接回车使用默认值
"""

from paper_crawler_core import run_pipeline

DEFAULTS = {
    "query": "retrieval augmented generation",
    "top": 2,
    "max_browse": 100,
    "max_llm": 5,
}


def _input(prompt: str, default) -> str | int:
    """交互式输入，直接回车返回默认值"""
    raw = input(f"  {prompt} (默认: {default}): ").strip()
    if not raw:
        return default
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            print(f"    输入无效，使用默认值 {default}")
            return default
    return raw


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  arXiv 论文爬取与智能筛选")
    print("  直接回车使用默认值")
    print("=" * 50 + "\n")

    query = _input("搜索关键词", DEFAULTS["query"])
    top_n = _input("保存论文篇数", DEFAULTS["top"])
    max_browse = _input("浏览arXiv结果上限(条)", DEFAULTS["max_browse"])
    max_llm = _input("大模型调用上限(次)", DEFAULTS["max_llm"])
    extra = input("  额外筛选要求 (可选，直接回车跳过): ").strip()

    print()

    run_pipeline(
        query=query,
        select_top_n=top_n,
        max_browse=max_browse,
        max_llm=max_llm,
        extra_query=extra if extra else "",
    )
