"""
模块1 - 核心实现：论文爬取与智能筛选
功能：关键词检索arXiv -> 日期过滤(近1个月) -> 读取摘要 -> 调用大模型筛选 -> 保存最合适的论文到本地
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# ==================== 配置 ====================
LLM_API_KEY = "sk-bexuAZV6AWw0dUcsFXtQPA"
LLM_BASE_URL = "https://models.sjtu.edu.cn/api/v1"
LLM_MODEL = "deepseek-chat"  # SJTU可用: deepseek-chat, qwen, glm, deepseek-v3.2, glm-5.1, minimax, qwen3.5-27b

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_SEARCH_URL = "https://arxiv.org/search/"
ARXIV_LIST_URL = "https://arxiv.org/list/"

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers")
DEFAULT_MAX_RESULTS = 10
DEFAULT_SELECT_TOP_N = 2
DEFAULT_DAYS = 30  # 默认只检索最近30天

ARXIV_RETRY = 3
ARXIV_DELAY = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ==================== 日期工具 ====================
def _parse_date(date_str: str) -> datetime | None:
    """尝试从各种格式中解析日期"""
    if not date_str:
        return None
    # 尝试完整日期格式
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(date_str[:len(fmt)], fmt)
        except ValueError:
            continue
    # 尝试年月格式 YYYY-MM（缺少日，默认取1号）
    try:
        return datetime.strptime(date_str[:7], "%Y-%m")
    except ValueError:
        pass
    # arXiv ID 格式如 2605.07935 -> 2026年5月
    m = re.search(r'(\d{2})(\d{2})\.(\d{4,5})', date_str)
    if m:
        try:
            yy, mm = int(m.group(1)), int(m.group(2))
            year = 2000 + yy if yy < 50 else 1900 + yy
            return datetime(year, mm, 1)
        except ValueError:
            pass
    return None


def _is_within_days(paper: dict, days: int) -> bool:
    """判断论文是否在指定天数内发表。无法解析日期的论文默认排除（严格模式）。"""
    if days <= 0:
        return True
    published = paper.get("published", "")
    dt = _parse_date(published)
    if dt is None:
        return False  # 无法解析日期则排除
    cutoff = datetime.now() - timedelta(days=days)
    return dt >= cutoff


# ==================== 1. arXiv 论文检索 ====================
def _build_api_query(keywords: str) -> str:
    """将用户输入的关键词构造为arXiv API搜索语句"""
    words = keywords.strip().split()
    if len(words) <= 1:
        return f"all:{keywords}"
    return " AND ".join(f'all:{w}' for w in words)


def _build_web_query(keywords: str) -> str:
    """将用户输入的关键词构造为arXiv网页搜索语句（网页搜索用自然语言更有效）"""
    return keywords.strip()


def search_arxiv_api(query: str, max_results: int, days: int) -> list[dict]:
    """方式1：通过arXiv API检索"""
    search_query = _build_api_query(query)
    # arXiv API支持日期范围过滤: submittedDate:[YYYYMMDD TO YYYYMMDD]
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        today = datetime.now().strftime("%Y%m%d")
        search_query += f' AND submittedDate:[{cutoff} TO {today}]'

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    for attempt in range(1, ARXIV_RETRY + 1):
        try:
            resp = requests.get(ARXIV_API_URL, params=params, timeout=30)
            resp.raise_for_status()
            papers = _parse_arxiv_api_xml(resp.text)
            return [p for p in papers if _is_within_days(p, days)]
        except requests.RequestException as e:
            print(f"    API请求失败(第{attempt}次): {e}")
            if attempt < ARXIV_RETRY:
                print(f"    {ARXIV_DELAY}秒后重试...")
                time.sleep(ARXIV_DELAY)

    return []


def _parse_arxiv_api_xml(xml_text: str) -> list[dict]:
    """解析arXiv API返回的Atom XML"""
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
        summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
        published = entry.find("atom:published", ns).text[:10]
        authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]

        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break

        entry_id = entry.find("atom:id", ns).text
        categories = [c.get("term") for c in entry.findall("atom:category", ns)]

        papers.append({
            "title": title,
            "authors": authors,
            "summary": summary,
            "pdf_url": pdf_url,
            "entry_id": entry_id,
            "published": published,
            "categories": categories,
        })
    return papers


def search_arxiv_web(query: str, max_results: int, days: int) -> list[dict]:
    """通过arXiv网页搜索 + 最新分类列表页补充，确保找到足够的近期论文"""
    search_query = _build_web_query(query)
    papers = []

    # 策略A：关键词搜索，按最新排序（不限制日期，后面统一过滤）
    params = {
        "query": search_query,
        "searchtype": "all",
        "start": 0,
        "order": "-announced_date_first",
    }
    try:
        resp = requests.get(ARXIV_SEARCH_URL, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        results = soup.select("li.arxiv-result")
        for result in results:
            paper = _parse_web_result(result)
            if paper:
                papers.append(paper)
    except Exception as e:
        print(f"    关键词搜索失败: {e}")

    # 策略B：从相关领域的最新列表页补充（确保有近期论文可筛选）
    category_map = {
        "finance": "q-fin", "financial": "q-fin", "option": "q-fin",
        "volatility": "q-fin", "trading": "q-fin", "risk": "q-fin",
        "portfolio": "q-fin", "derivative": "q-fin", "pricing": "q-fin",
        "language model": "cs.CL", "llm": "cs.CL", "nlp": "cs.CL",
        "agent": "cs.AI", "reinforcement learning": "cs.LG",
        "computer vision": "cs.CV", "image": "cs.CV",
        "machine learning": "cs.LG", "deep learning": "cs.LG",
        "robot": "cs.RO", "transformer": "cs.LG",
    }

    target_cats = []
    query_lower = query.lower()
    for kw, cat in category_map.items():
        if kw in query_lower and cat not in target_cats:
            target_cats.append(cat)
    if not target_cats:
        target_cats = ["cs.AI"]

    for cat in target_cats:
        try:
            list_papers = _scrape_category_list(cat, days, max_results * 2)
            papers.extend(list_papers)
        except Exception:
            pass

    # 去重（按entry_id）
    seen = set()
    unique_papers = []
    for p in papers:
        eid = p.get("entry_id", "")
        if eid not in seen:
            seen.add(eid)
            unique_papers.append(p)

    # 日期过滤
    if days > 0:
        filtered = [p for p in unique_papers if _is_within_days(p, days)]
    else:
        filtered = unique_papers

    return filtered[:max_results]


def _parse_web_result(result) -> dict | None:
    """解析arXiv网页搜索的单个结果"""
    title_tag = result.select_one("p.title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        return None

    abstract_tag = result.select_one("span.abstract-short")
    summary = abstract_tag.get_text(strip=True) if abstract_tag else ""
    summary = re.sub(r'\s*▽\s*More\s*$', '', summary)

    authors = []
    authors_tag = result.select_one("p.authors")
    if authors_tag:
        authors = [a.get_text(strip=True) for a in authors_tag.select("a")]

    link_tag = result.select_one("p.list-title a")
    entry_id = link_tag.get("href", "") if link_tag else ""
    pdf_url = entry_id.replace("/abs/", "/pdf/") if entry_id else ""

    # 从arXiv ID推算日期 (如 2605.07935 -> 2026-05)
    published = ""
    arxiv_id_tag = result.select_one("p.list-title")
    if arxiv_id_tag:
        id_text = arxiv_id_tag.get_text()
        id_match = re.search(r'(\d{2})(\d{2})\.\d{4,5}', id_text)
        if id_match:
            yy, mm = int(id_match.group(1)), int(id_match.group(2))
            year = 2000 + yy if yy < 50 else 1900 + yy
            published = f"{year}-{mm:02d}"

    categories = [tag.get_text(strip=True) for tag in result.select("span.primary-subject")]

    return {
        "title": title,
        "authors": authors,
        "summary": summary,
        "pdf_url": pdf_url,
        "entry_id": entry_id,
        "published": published,
        "categories": categories,
    }


def _scrape_category_list(category: str, days: int, max_count: int) -> list[dict]:
    """从arXiv分类列表页抓取最新论文（如 https://arxiv.org/list/q-fin/recent）"""
    url = f"{ARXIV_LIST_URL}{category}/recent"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    dts = soup.select("dt")
    dds = soup.select("dd")

    papers = []
    cutoff = datetime.now() - timedelta(days=days) if days > 0 else None

    for i in range(min(len(dts), len(dds))):
        dt, dd = dts[i], dds[i]

        # 提取arXiv ID
        id_tag = dt.select_one('a[title="Abstract"]')
        if not id_tag:
            continue
        arxiv_id = id_tag.get_text(strip=True).replace("arXiv:", "")
        entry_id = f"https://arxiv.org/abs/{arxiv_id}"
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        # 从ID解析日期
        published = ""
        id_match = re.match(r'(\d{2})(\d{2})\.\d{4,5}', arxiv_id)
        if id_match:
            yy, mm = int(id_match.group(1)), int(id_match.group(2))
            year = 2000 + yy if yy < 50 else 1900 + yy
            published = f"{year}-{mm:02d}"

        # 标题
        title_tag = dd.select_one("div.list-title")
        title = title_tag.get_text(strip=True).replace("Title:", "").strip() if title_tag else ""
        if not title:
            continue

        # 作者
        authors_tag = dd.select_one("div.list-authors")
        authors = []
        if authors_tag:
            authors = [a.get_text(strip=True) for a in authors_tag.select("a")]

        # 分类
        categories_tag = dd.select_one("span.primary-subject")
        categories = [categories_tag.get_text(strip=True)] if categories_tag else [category]

        paper = {
            "title": title,
            "authors": authors,
            "summary": "",  # 列表页无摘要，后续补充
            "pdf_url": pdf_url,
            "entry_id": entry_id,
            "published": published,
            "categories": categories,
        }

        if cutoff is not None and not _is_within_days(paper, days):
            continue

        papers.append(paper)
        if len(papers) >= max_count:
            break

    return papers


def _enrich_abstracts(papers: list[dict]) -> list[dict]:
    """对缺少摘要的论文，访问论文页面获取完整摘要"""
    for paper in papers:
        if not paper.get("summary") or len(paper.get("summary", "")) < 50:
            if not paper.get("entry_id"):
                continue
            try:
                resp = requests.get(paper["entry_id"], headers=HEADERS, timeout=15)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    abstract_tag = soup.select_one("blockquote.abstract")
                    if not abstract_tag:
                        # 备用选择器
                        abstract_tag = soup.select_one("#abstract")
                    if abstract_tag:
                        full_text = abstract_tag.get_text(strip=True)
                        full_text = re.sub(r'^Abstract[:\s]*', '', full_text).strip()
                        paper["summary"] = full_text
                time.sleep(1)
            except Exception:
                pass
    return papers


def search_arxiv(query: str, max_results: int = DEFAULT_MAX_RESULTS, days: int = DEFAULT_DAYS) -> list[dict]:
    """根据关键词检索arXiv，优先网页搜索，失败则尝试API。自动过滤超出日期范围的论文。"""
    if days > 0:
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        print(f"[1] 正在检索arXiv，关键词: {query}，最多 {max_results} 篇，仅限近 {days} 天({cutoff_date}之后)...")
    else:
        print(f"[1] 正在检索arXiv，关键词: {query}，最多 {max_results} 篇...")

    # 优先使用网页搜索（更稳定）
    try:
        papers = search_arxiv_web(query, max_results, days)
        if papers:
            papers = _enrich_abstracts(papers)
            print(f"    (网页搜索)检索到 {len(papers)} 篇论文")
            for i, p in enumerate(papers, 1):
                print(f"    [{i}] {p['title'][:70]}  ({p.get('published', '?')})")
            return papers
    except Exception as e:
        print(f"    网页搜索失败: {e}")

    # 备用：API方式
    print("    切换到API方式...")
    papers = search_arxiv_api(query, max_results, days)
    if papers:
        print(f"    (API)检索到 {len(papers)} 篇论文")
        for i, p in enumerate(papers, 1):
            print(f"    [{i}] {p['title'][:70]}  ({p.get('published', '?')})")
        return papers

    print("    所有检索方式均失败，请稍后重试。")
    return []


# ==================== 2. 大模型筛选 ====================
def filter_papers_with_llm(
    papers: list[dict],
    query: str,
    select_top_n: int = DEFAULT_SELECT_TOP_N,
) -> list[dict]:
    """调用大模型根据摘要判断论文与检索意图的匹配度，综合相关度、创新性、学术价值筛选"""
    print(f"[2] 正在调用大模型筛选论文，目标筛选 {select_top_n} 篇...")

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    papers_info = ""
    for i, p in enumerate(papers, 1):
        papers_info += f"\n--- 论文 {i} ---\n"
        papers_info += f"标题: {p['title']}\n"
        authors_str = ', '.join(p['authors'][:5]) + ('等' if len(p['authors']) > 5 else '')
        papers_info += f"作者: {authors_str}\n"
        papers_info += f"摘要: {p['summary'][:800]}\n"
        papers_info += f"发布日期: {p['published']}\n"
        papers_info += f"分类: {', '.join(p['categories'][:3])}\n"

    prompt = f"""你是一位资深科研文献筛选助手。用户正在检索与以下关键词相关的最新论文：

关键词: {query}

以下是从arXiv检索到的 {len(papers)} 篇论文，请综合以下维度筛选出最合适的 {select_top_n} 篇论文：
1. 与关键词的匹配度（最重要）
2. 研究创新性
3. 学术价值与影响力
4. 作者/机构水平（知名大学或研究机构优先）

{papers_info}

请严格按照以下JSON格式输出你的筛选结果（不要输出任何其他内容）：
```json
{{
  "selected": [
    {{
      "index": 1,
      "relevance_score": 95,
      "reason": "简要说明选择理由（一句话）"
    }}
  ]
}}
```

要求：
1. index 为论文编号（从1开始）
2. relevance_score 为0-100的综合评分
3. 按评分从高到低排列
4. 最多选择 {select_top_n} 篇
5. 仅输出JSON，不要输出其他文字"""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        content = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    大模型调用失败: {e}")
        print("    降级策略：直接返回前 {} 篇论文".format(select_top_n))
        return papers[:select_top_n]

    # 解析大模型返回的JSON
    try:
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        result = json.loads(content)
        selected_indices = [item["index"] - 1 for item in result["selected"]]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"    JSON解析失败: {e}")
        print(f"    原始返回: {content[:200]}")
        print("    降级策略：直接返回前 {} 篇论文".format(select_top_n))
        return papers[:select_top_n]

    selected_papers = []
    for idx in selected_indices:
        if 0 <= idx < len(papers):
            paper = papers[idx].copy()
            matching_item = next((item for item in result["selected"] if item["index"] - 1 == idx), None)
            if matching_item:
                paper["relevance_score"] = matching_item["relevance_score"]
                paper["selection_reason"] = matching_item["reason"]
            selected_papers.append(paper)

    print(f"    筛选完成，选中 {len(selected_papers)} 篇")
    for i, p in enumerate(selected_papers, 1):
        print(f"    [{i}] {p['title'][:60]}... (评分: {p.get('relevance_score', 'N/A')})")

    return selected_papers


# ==================== 3. 保存到本地 ====================
def save_papers(papers: list[dict], query: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> list[str]:
    """将筛选后的论文信息保存为JSON文件"""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = re.sub(r'[^\w]', '_', query)[:30]

    saved_files = []
    for i, paper in enumerate(papers, 1):
        filename = f"{safe_query}_{timestamp}_{i}.json"
        filepath = os.path.join(output_dir, filename)

        paper["search_query"] = query
        paper["crawled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(paper, f, ensure_ascii=False, indent=2)

        saved_files.append(filepath)
        print(f"    已保存: {filepath}")

    return saved_files


# ==================== 主流程 ====================
def run_pipeline(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    select_top_n: int = DEFAULT_SELECT_TOP_N,
    days: int = DEFAULT_DAYS,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> list[str]:
    """
    运行完整流水线：检索 -> 日期过滤 -> 筛选 -> 保存

    参数:
        query: 检索关键词
        max_results: arXiv检索返回的最大论文数 (默认10)
        select_top_n: 筛选后保留的论文数 (默认2)
        days: 只保留最近N天内发表的论文，0表示不限 (默认30)
        output_dir: 保存目录

    返回:
        保存的文件路径列表
    """
    print("=" * 60)
    print(f"  论文爬取与智能筛选流水线")
    print(f"  关键词: {query}")
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        print(f"  时间范围: 近{days}天 ({cutoff} 之后)")
    print("=" * 60)

    # Step 1: 检索arXiv（含日期过滤）
    papers = search_arxiv(query, max_results, days)
    if not papers:
        print("未检索到符合条件的论文，请更换关键词或放宽时间范围重试。")
        return []

    # Step 2: 大模型筛选
    selected = filter_papers_with_llm(papers, query, select_top_n)
    if not selected:
        print("筛选未选中任何论文。")
        return []

    # Step 3: 保存到本地
    saved_files = save_papers(selected, query, output_dir)

    print("=" * 60)
    print(f"  完成！共保存 {len(saved_files)} 篇论文到 {output_dir}")
    print("=" * 60)
    return saved_files