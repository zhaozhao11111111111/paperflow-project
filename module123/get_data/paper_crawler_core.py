"""
模块1 - 核心实现：论文爬取与智能筛选（Selenium版）

完全模拟人对arXiv的搜索行为：
1. 用Selenium打开Chrome浏览器，访问arXiv搜索页
2. 输入关键词并搜索
3. 逐条浏览搜索结果，根据arXiv ID判断年月，只关注最近一个月的文章
4. 对符合日期的文章，读取标题和摘要，调用大模型判断是否符合搜索要求
5. 如果符合且本地未保存过，则保存到本地，然后继续浏览下一条
6. 否则继续，直到搜索完所有符合日期的文章为止
7. 维护一份quick_result.md，记录已保存的论文信息
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from openai import OpenAI
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==================== 配置 ====================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers")
DEFAULT_SELECT_TOP_N = 2


# ==================== 日期工具 ====================
def _get_recent_months(n: int = 1) -> list[str]:
    """获取最近n个月的YYMM列表。

    例如当前2026年5月，n=1 -> ['2604', '2605']
    """
    now = datetime.now()
    months = []
    for delta in range(n + 1):
        d = now.month - delta
        y = now.year
        while d <= 0:
            d += 12
            y -= 1
        months.append(f"{y % 100:02d}{d:02d}")
    return months


def _arxiv_id_in_recent(arxiv_id: str, recent_months: list[str]) -> bool:
    """判断arXiv ID（如 2605.00054）是否在最近月份列表中。"""
    match = re.match(r'(\d{4})\.\d{4,5}', arxiv_id)
    if match:
        yymm = match.group(1)
        return yymm in recent_months
    return False


def _extract_arxiv_id_from_url(url: str) -> str | None:
    """从URL中提取arXiv ID，如 https://arxiv.org/abs/2605.00054 -> 2605.00054"""
    match = re.search(r'(\d{4}\.\d{4,5})', url)
    return match.group(1) if match else None


def _arxiv_id_to_date(arxiv_id: str) -> str:
    """将arXiv ID转换为日期字符串，如 2605.00054 -> 2026-05"""
    match = re.match(r'(\d{2})(\d{2})\.\d{4,5}', arxiv_id)
    if match:
        yy, mm = int(match.group(1)), int(match.group(2))
        year = 2000 + yy if yy < 50 else 1900 + yy
        return f"{year}-{mm:02d}"
    return ""


# ==================== 已保存论文管理 ====================
def _load_saved_ids(output_dir: str) -> set[str]:
    """读取本地已保存论文的arXiv ID集合，避免重复保存"""
    saved = set()
    if not os.path.exists(output_dir):
        return saved
    for fname in os.listdir(output_dir):
        if fname.endswith('.json'):
            filepath = os.path.join(output_dir, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                arxiv_id = data.get("arxiv_id", "")
                if arxiv_id:
                    saved.add(arxiv_id)
            except Exception:
                pass
    return saved


# ==================== 已检查论文管理（大模型调用过的） ====================
_CHECKED_FILE = "checked.json"


def _load_checked_ids(output_dir: str) -> set[str]:
    """读取已被大模型检查过的论文ID集合，避免重复调用"""
    path = os.path.join(output_dir, _CHECKED_FILE)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_checked_ids(output_dir: str, checked_ids: set[str]):
    """保存已检查论文ID集合到文件"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, _CHECKED_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted(checked_ids), f, ensure_ascii=False, indent=2)


# ==================== 大模型筛选 ====================
def _llm_check_paper(query: str, title: str, abstract: str, client: OpenAI, extra_query: str = "") -> dict | None:
    """调用大模型判断单篇论文是否符合搜索要求。

    返回: {"match": true/false, "reason": "..."} 或 None（调用失败时）
    """
    extra_block = ""
    if extra_query:
        extra_block = f"""
用户的额外筛选要求: {extra_query}

在判断匹配度时，除基本关键词匹配外，还必须同时满足上述额外要求。"""

    prompt = f"""你是一位资深科研文献筛选助手。

用户搜索关键词: {query}
{extra_block}
当前论文:
标题: {title}
摘要: {abstract[:600]}

请判断这篇论文是否与用户搜索关键词高度相关。综合考量：
1. 论文主题与关键词的匹配度
2. 研究的创新性和学术价值

请严格按以下JSON格式输出（不要输出任何其他内容）：
```json
{{
  "match": true,
  "reason": "简要说明匹配理由"
}}
```
或：
```json
{{
  "match": false,
  "reason": "简要说明不匹配理由"
}}
```"""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()

        # 解析JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        result = json.loads(content)
        return result
    except Exception as e:
        print(f"      大模型调用失败: {e}")
        return None


# ==================== 保存论文 ====================
def _save_paper(paper: dict, query: str, output_dir: str) -> str:
    """保存单篇论文到本地JSON文件"""
    os.makedirs(output_dir, exist_ok=True)

    arxiv_id = paper.get("arxiv_id", "unknown")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = arxiv_id.replace(".", "_")
    filename = f"{safe_id}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    paper["search_query"] = query
    paper["crawled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(paper, f, ensure_ascii=False, indent=2)

    return filepath


# ==================== quick_result.md 维护 ====================
def _update_readme(output_dir: str):
    """扫描papers目录下所有JSON文件，按搜索关键词分类生成/更新quick_result.md"""
    readme_path = os.path.join(output_dir, "quick_result.md")

    papers = []
    if os.path.exists(output_dir):
        for fname in sorted(os.listdir(output_dir)):
            if not fname.endswith('.json'):
                continue
            filepath = os.path.join(output_dir, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                papers.append({
                    "arxiv_id": data.get("arxiv_id", ""),
                    "title": data.get("title", ""),
                    "authors": data.get("authors", []),
                    "published": data.get("published", ""),
                    "search_query": data.get("search_query", "未分类"),
                    "entry_id": data.get("entry_id", ""),
                    "pdf_url": data.get("pdf_url", ""),
                    "crawled_at": data.get("crawled_at", ""),
                    "reason": data.get("selection_reason", data.get("reason", "")),
                    "filename": fname,
                })
            except Exception:
                continue

    # 按搜索关键词分组，保持各组内按爬取时间排序
    from collections import OrderedDict

    def _md_anchor(text: str) -> str:
        """生成 markdown 标题锚点"""
        return re.sub(r'[^\w\s-]', '', text.lower()).strip().replace(' ', '-')
    groups = OrderedDict()
    for p in papers:
        key = p["search_query"]
        groups.setdefault(key, []).append(p)

    lines = [
        "# 已保存论文列表\n",
        "",
        f"共 {len(papers)} 篇论文，{len(groups)} 个搜索关键词 | 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "",
    ]

    # 目录
    lines.append("## 目录\n\n")
    for query, group in groups.items():
        lines.append(f"- [{query}](#{_md_anchor(query)}) ({len(group)} 篇)\n")
    lines.append("\n---\n\n")

    # 按关键词分组输出
    for query, group in groups.items():
        lines.append(f"## {query}\n\n")
        for i, p in enumerate(group, 1):
            authors_str = ', '.join(p["authors"][:5])
            if len(p["authors"]) > 5:
                authors_str += ' 等'

            lines.append(f"### {i}. {p['title']}\n")
            lines.append(f"- **arXiv ID**: [{p['arxiv_id']}]({p['entry_id']})")
            lines.append(f"\n- **作者**: {authors_str}")
            lines.append(f"\n- **发表日期**: {p['published']}")
            lines.append(f"\n- **PDF**: [{p['pdf_url']}]({p['pdf_url']})")
            if p["reason"]:
                lines.append(f"\n- **入选理由**: {p['reason']}")
            lines.append(f"\n- **爬取时间**: {p['crawled_at']}")
            lines.append(f"\n- **文件**: `{p['filename']}`")
            lines.append("\n\n")
        lines.append("---\n\n")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))

    print(f"    quick_result.md已更新: {readme_path}")


# ==================== Selenium 爬虫核心 ====================
def _create_driver() -> webdriver.Chrome:
    """创建Chrome浏览器实例"""
    options = Options()
    if os.getenv("PAPERFLOW_HEADLESS", "").lower() in ("1", "true", "yes"):
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    return driver


def _search_arxiv(driver: webdriver.Chrome, query: str):
    """在arXiv搜索页输入关键词并搜索"""
    driver.get("https://arxiv.org/search/")
    time.sleep(3)

    # 页面有多个 name="query" 的输入框，用 id="query" 精确定位主搜索框
    search_input = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.ID, "query"))
    )
    search_input.clear()
    search_input.send_keys(query)
    time.sleep(1)

    # 点击搜索按钮
    search_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button.button.is-link.is-medium"))
    )
    search_btn.click()

    # 等待搜索结果加载完成
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "li.arxiv-result, p.is-size-5"))
    )
    time.sleep(2)


def _get_search_result_links(driver: webdriver.Chrome) -> list[dict]:
    """从搜索结果页提取所有论文链接和基本信息"""
    papers = []

    # 等待搜索结果加载
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "li.arxiv-result"))
        )
    except Exception:
        return papers

    results = driver.find_elements(By.CSS_SELECTOR, "li.arxiv-result")

    for result in results:
        try:
            # 标题：纯文本在 <p class="title ..."> 中，不是链接
            title_tag = result.find_element(By.CSS_SELECTOR, "p.title")
            title = title_tag.text.strip()
            if not title:
                continue

            # arXiv ID 和链接：在 <p class="list-title ..."><a href="...">arXiv:XXXX.XXXXX</a></p>
            id_link = result.find_element(By.CSS_SELECTOR, "p.list-title a")
            href = id_link.get_attribute("href")
            arxiv_id = _extract_arxiv_id_from_url(href)
            if not arxiv_id:
                # 备用：从链接文本提取，如 "arXiv:2605.22642"
                id_text = id_link.text.strip()
                arxiv_id = _extract_arxiv_id_from_url(id_text)

            if not arxiv_id:
                continue

            # 摘要：优先用完整摘要（隐藏的 span，Selenium 仍可读取）
            abstract = ""
            try:
                abstract_tag = result.find_element(By.CSS_SELECTOR, "span.abstract-full")
                abstract = abstract_tag.text.strip()
                abstract = re.sub(r'\s*▲\s*Less\s*$', '', abstract)
                abstract = re.sub(r'\s*Hide\s*$', '', abstract)
            except Exception:
                pass
            if len(abstract) < 20:
                try:
                    abstract_tag = result.find_element(By.CSS_SELECTOR, "span.abstract-short")
                    abstract = abstract_tag.text.strip()
                    abstract = re.sub(r'\s*▽\s*More\s*$', '', abstract)
                    abstract = re.sub(r'\s*Show more\s*$', '', abstract)
                except Exception:
                    pass

            # 作者
            try:
                author_tags = result.find_elements(By.CSS_SELECTOR, "p.authors a")
                authors = [a.text.strip() for a in author_tags if a.text.strip()]
            except Exception:
                authors = []

            papers.append({
                "title": title,
                "arxiv_id": arxiv_id,
                "abstract": abstract,
                "authors": authors,
                "entry_id": href,
                "pdf_url": href.replace("/abs/", "/pdf/"),
            })
        except Exception:
            continue

    return papers


def _go_to_next_page(driver: webdriver.Chrome) -> bool:
    """尝试翻到下一页，返回是否成功"""
    try:
        next_btn = driver.find_element(By.CSS_SELECTOR, "a.pagination-next")
        next_btn.click()
        time.sleep(3)
        return True
    except Exception:
        return False


def _get_paper_abstract_from_detail(driver: webdriver.Chrome) -> str:
    """从论文详情页获取完整摘要"""
    try:
        abstract_block = driver.find_element(By.CSS_SELECTOR, "blockquote.abstract")
        text = abstract_block.text.strip()
        text = re.sub(r'^Abstract[:\s]*', '', text)
        return text
    except Exception:
        return ""


# ==================== 主流程 ====================
def run_pipeline(
    query: str,
    max_results: int = 20,
    select_top_n: int = DEFAULT_SELECT_TOP_N,
    days: int = 30,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    extra_query: str = "",
    max_browse: int = 100,
    max_llm: int = 5,
) -> list[str]:
    """
    运行完整流水线：Selenium模拟搜索 -> 逐条浏览 -> 先查本地去重 -> 大模型判断 -> 保存 -> 更新quick_result.md

    流程：对每篇论文先判断日期、再判断本地是否已保存，两者都通过后才调用大模型。
    终止条件（达到任一即停）：
        - 已浏览 max_browse 条arXiv搜索结果
        - 已调用大模型 max_llm 次
        - 已保存够select_top_n篇

    参数:
        query: 检索关键词
        max_results: 保留参数兼容性
        select_top_n: 最多保存的论文数 (默认2)，保存够即停止
        days: 保留参数兼容性，实际使用arXiv ID前缀判断日期
        output_dir: 保存目录
        extra_query: 额外筛选要求，附加到大模型提示词中
        max_browse: 最多浏览的arXiv搜索结果条数 (默认100)
        max_llm: 最多调用大模型的次数 (默认5)

    返回:
        保存的文件路径列表
    """
    recent_months = _get_recent_months(1)
    saved_ids = _load_saved_ids(output_dir)
    checked_ids = _load_checked_ids(output_dir)
    saved_count = 0
    saved_files = []
    checked_count = 0
    skipped_old = 0
    browsed_count = 0
    llm_call_count = 0
    last_browsed_date = ""

    llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    print("=" * 60)
    print(f"  论文爬取与智能筛选流水线 (Selenium版)")
    print(f"  关键词: {query}")
    if extra_query:
        print(f"  额外要求: {extra_query}")
    print(f"  时间范围: 最近1个月 (arXiv ID前缀: {', '.join(recent_months)})")
    print(f"  目标保存: {select_top_n} 篇")
    print(f"  浏览上限: {max_browse} 条 | 大模型调用上限: {max_llm} 次")
    print(f"  本地已有: {len(saved_ids)} 篇已保存，{len(checked_ids)} 篇已检查")
    print("=" * 60)

    driver = _create_driver()

    try:
        # Step 1: 搜索
        print("\n[1] 正在打开arXiv并搜索...")
        _search_arxiv(driver, query)
        print("    搜索完成")

        # Step 2: 逐条浏览搜索结果
        page = 1
        while saved_count < select_top_n:
            print(f"\n[2] 正在浏览第 {page} 页搜索结果...")

            papers = _get_search_result_links(driver)
            if not papers:
                print("    当前页无搜索结果")
                break

            all_old_on_page = True
            for i, paper in enumerate(papers, 1):
                # 检查终止条件
                if saved_count >= select_top_n:
                    break
                browsed_count += 1
                if browsed_count > max_browse:
                    print(f"\n    已浏览 {max_browse} 条arXiv结果，达到上限，停止搜索")
                    break

                arxiv_id = paper["arxiv_id"]

                # 检查日期：arXiv ID前4位是否在recent_months中
                if not _arxiv_id_in_recent(arxiv_id, recent_months):
                    skipped_old += 1
                    if skipped_old <= 3:
                        print(f"    [{i}] {arxiv_id} - 不在时间范围内，跳过")
                    elif skipped_old == 4:
                        print(f"    ... (更多旧文章已跳过)")
                    continue

                all_old_on_page = False
                checked_count += 1
                last_browsed_date = _arxiv_id_to_date(arxiv_id)

                # 检查是否已保存过
                if arxiv_id in saved_ids:
                    print(f"    [{i}] {arxiv_id} - 已保存过，跳过")
                    continue

                title = paper["title"]
                abstract = paper.get("abstract", "")

                # 如果摘要太短，进入详情页获取完整摘要
                if len(abstract) < 50:
                    print(f"    [{i}] {arxiv_id} - 摘要太短，正在进入详情页获取...")
                    try:
                        driver.execute_script(f"window.open('{paper['entry_id']}', '_blank');")
                        time.sleep(2)

                        driver.switch_to.window(driver.window_handles[-1])
                        full_abstract = _get_paper_abstract_from_detail(driver)
                        if full_abstract:
                            abstract = full_abstract
                        driver.close()
                        driver.switch_to.window(driver.window_handles[0])
                        time.sleep(1)
                    except Exception as e:
                        print(f"      读取详情页失败: {e}")
                        try:
                            driver.close()
                            driver.switch_to.window(driver.window_handles[0])
                        except Exception:
                            pass

                print(f"    [{i}] {arxiv_id} - {title[:55]}...")

                # 检查是否已被大模型检查过
                if arxiv_id in checked_ids:
                    print(f"    [{i}] {arxiv_id} - 已检查过（未匹配），跳过")
                    continue

                # 调用大模型判断（达到上限则跳出整个循环）
                if llm_call_count >= max_llm:
                    break
                llm_call_count += 1
                print(f"         正在调用大模型判断... ({llm_call_count}/{max_llm})")
                result = _llm_check_paper(query, title, abstract, llm_client, extra_query)

                if result is None:
                    print(f"         大模型调用失败，跳过")
                    continue

                # 记录已检查（无论匹配与否）
                checked_ids.add(arxiv_id)

                if result.get("match", False):
                    # 保存论文
                    paper["abstract"] = abstract
                    paper["published"] = _arxiv_id_to_date(arxiv_id)
                    filepath = _save_paper(paper, query, output_dir)
                    saved_files.append(filepath)
                    saved_ids.add(arxiv_id)
                    saved_count += 1
                    reason = result.get("reason", "")
                    print(f"         *** 匹配！已保存 ({saved_count}/{select_top_n}) - {reason}")

                    # 更新README
                    _update_readme(output_dir)
                else:
                    reason = result.get("reason", "")
                    print(f"         不匹配 - {reason[:50]}")

            # 检查是否需要翻页
            if saved_count >= select_top_n:
                print(f"\n    已达到目标保存数量({select_top_n}篇)，停止搜索")
                break

            if browsed_count > max_browse:
                print(f"\n    已浏览 {browsed_count-1} 条arXiv结果，达到上限({max_browse})，停止搜索")
                break

            if llm_call_count >= max_llm:
                print(f"\n    大模型已调用 {llm_call_count} 次，达到上限({max_llm})，停止搜索")
                break

            # 如果当前页所有文章都超出时间范围，后面也不会有新的了
            if all_old_on_page:
                print(f"\n    当前页所有文章均已超出时间范围，停止搜索")
                break

            # 尝试翻页
            print(f"\n    尝试翻到下一页...")
            if not _go_to_next_page(driver):
                print("    已到最后一页")
                break
            page += 1

    except Exception as e:
        print(f"\n    运行出错: {e}")
    finally:
        print("\n    正在关闭浏览器...")
        driver.quit()

    # 最终确保README是最新的，并保存已检查论文记录
    _update_readme(output_dir)
    _save_checked_ids(output_dir, checked_ids)

    # 汇总
    print("\n" + "=" * 60)
    print(f"  搜索完成！")
    print(f"  浏览arXiv结果: {browsed_count} 条 (上限 {max_browse})")
    print(f"  大模型调用: {llm_call_count} 次 (上限 {max_llm})")
    print(f"  符合日期的文章: {checked_count} 篇")
    print(f"  已检查(累计): {len(checked_ids)} 篇")
    print(f"  新保存: {saved_count} 篇")
    if last_browsed_date:
        print(f"  最后浏览文章日期: {last_browsed_date}")
    if saved_files:
        print(f"  保存位置: {output_dir}")
        for f in saved_files:
            print(f"    - {os.path.basename(f)}")
    else:
        print("  未找到符合要求的新论文")
    print("=" * 60)

    return saved_files
