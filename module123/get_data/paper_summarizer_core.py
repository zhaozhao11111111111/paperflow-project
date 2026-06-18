"""
模块2 - 核心实现：论文 → 结构化 Markdown 总结

读取模块1保存的论文 JSON，补全摘要（必要时下载 PDF 提取正文），
调用大模型生成包含背景、方法、结论、创新点等章节的 Markdown 文件。
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
from openai import OpenAI

# ==================== 配置（与模块1保持一致） ====================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

DEFAULT_PAPERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers")
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summaries")
SUMMARIZED_INDEX_FILE = "summarized.json"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


# ==================== 索引管理 ====================
def _load_summarized_index(output_dir: str) -> dict[str, str]:
    """读取已总结论文索引：arxiv_id -> markdown 相对路径"""
    path = os.path.join(output_dir, SUMMARIZED_INDEX_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_summarized_index(output_dir: str, index: dict[str, str]):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, SUMMARIZED_INDEX_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ==================== 论文加载 ====================
def _load_paper_json(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_paper_json_files(papers_dir: str) -> list[str]:
    if not os.path.exists(papers_dir):
        return []
    files = []
    for fname in sorted(os.listdir(papers_dir)):
        if fname.endswith(".json") and fname not in (SUMMARIZED_INDEX_FILE, "checked.json"):
            files.append(os.path.join(papers_dir, fname))
    return files


def _resolve_arxiv_id(paper: dict) -> str:
    arxiv_id = paper.get("arxiv_id", "").strip()
    if arxiv_id:
        return arxiv_id
    for key in ("entry_id", "pdf_url"):
        url = paper.get(key, "")
        match = re.search(r"(\d{4}\.\d{4,5})", url)
        if match:
            return match.group(1)
    return ""


def _is_abstract_truncated(abstract: str) -> bool:
    if not abstract:
        return True
    if "…" in abstract or "..." in abstract:
        return True
    return len(abstract) < 120


# ==================== arXiv 摘要补全 ====================
def _fetch_abstract_from_arxiv_api(arxiv_id: str) -> str:
    """通过 arXiv API 获取完整摘要"""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entry = root.find("atom:entry", ATOM_NS)
        if entry is None:
            return ""
        summary = entry.find("atom:summary", ATOM_NS)
        if summary is None or not summary.text:
            return ""
        return re.sub(r"\s+", " ", summary.text.strip())
    except Exception as e:
        print(f"      arXiv API 获取摘要失败: {e}")
        return ""


def _enrich_paper_metadata(paper: dict) -> dict:
    """补全 arXiv ID、摘要等字段"""
    enriched = dict(paper)
    arxiv_id = _resolve_arxiv_id(enriched)
    enriched["arxiv_id"] = arxiv_id

    if arxiv_id and not enriched.get("entry_id"):
        enriched["entry_id"] = f"https://arxiv.org/abs/{arxiv_id}"
    if arxiv_id and not enriched.get("pdf_url"):
        enriched["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"

    abstract = enriched.get("abstract", "").strip()
    if arxiv_id and _is_abstract_truncated(abstract):
        print(f"      摘要不完整，正在从 arXiv API 补全...")
        full_abstract = _fetch_abstract_from_arxiv_api(arxiv_id)
        if full_abstract:
            enriched["abstract"] = full_abstract
            enriched["abstract_source"] = "arxiv_api"
        else:
            enriched["abstract"] = abstract
            enriched["abstract_source"] = "local_json"
    else:
        enriched["abstract"] = abstract
        enriched["abstract_source"] = "local_json"

    return enriched


# ==================== PDF 处理：按 Figure/Table 标题裁剪渲染 ====================
CAPTION_LINE_RE = re.compile(
    r"^\s*(Figure|Fig\.|Table)\s*(\d+)\s*[:\.]\s*(.+)$",
    re.IGNORECASE,
)
SECTION_HEADING_RE = re.compile(r"^\d+(?:\.\d+)+\s*[\w\u4e00-\u9fff]")
PROSE_START_RE = re.compile(
    r"^(consistent with|The |This |Qualitative |In contrast|However|Additionally|We |Our |•|–)",
    re.IGNORECASE,
)
TABLE_ROW_HINT_RE = re.compile(
    r"(^Note:|^Method|Input:|Chunks:|±|\d+\.\d+\s*±|Khmer-Aware|Recursive|Sentence-Based|LLM-Based)",
    re.IGNORECASE,
)


def _download_pdf(pdf_url: str, save_path: str) -> bool:
    try:
        resp = requests.get(pdf_url, timeout=60, headers={"User-Agent": "paper-summarizer/1.0"})
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"      PDF 下载失败: {e}")
        return False


def _page_text_blocks(page) -> list[dict]:
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        text = "".join(s["text"] for line in block["lines"] for s in line["spans"]).strip()
        if text:
            blocks.append({"text": text, "bbox": block["bbox"]})
    blocks.sort(key=lambda b: b["bbox"][1])
    return blocks


def _find_captions_on_page(page, page_num: int) -> list[dict]:
    """逐行识别 Figure/Table 标题及其位置"""
    captions = []
    seen: set[str] = set()
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        block_text = "".join(
            span["text"]
            for line in block["lines"]
            for span in line["spans"]
        ).strip()
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text or text in seen:
                continue
            m = CAPTION_LINE_RE.match(text)
            if not m:
                continue
            kind_raw = m.group(1).lower()
            kind = "table" if kind_raw.startswith("tab") else "figure"
            captions.append({
                "kind": kind,
                "num": int(m.group(2)),
                "caption": block_text or text,
                "bbox": block["bbox"],
                "page": page_num,
            })
            seen.add(text)
    captions.sort(key=lambda item: item["bbox"][1])
    return captions


def _figure_clip_rect(page, cap_bbox: tuple) -> "fitz.Rect":
    """Figure：优先按 PDF 图片块边界裁剪，固定区域仅作兜底。"""
    import fitz

    image_blocks = [
        block["bbox"]
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 1 and block["bbox"][3] <= cap_bbox[1] + 4
    ]
    if image_blocks:
        nearest = max(image_blocks, key=lambda bbox: bbox[3])
        return fitz.Rect(
            max(0, nearest[0] - 4),
            max(0, nearest[1] - 4),
            min(page.rect.width, nearest[2] + 4),
            min(cap_bbox[1] - 2, nearest[3] + 4),
        )

    margin_x = 54
    top = 72
    bottom = cap_bbox[1] - 8
    if bottom - top < 100:
        top = max(50, cap_bbox[1] - 420)
    return fitz.Rect(margin_x, top, page.rect.width - margin_x, bottom)


def _get_table_ruling_lines(page) -> list:
    """提取 PDF 中表格的横向边框线"""
    lines = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.width >= 180 and rect.height < 3:
            lines.append(rect)
    lines.sort(key=lambda r: r.y0)
    return lines


def _table_clip_from_rulings(
    page,
    cap_bbox: tuple,
    blocks: list[dict],
    boundary_y: float | None = None,
):
    """优先用表格横线确定裁剪框，避免把下方正文段落裁进去"""
    import fitz

    cap_bottom = cap_bbox[3]
    max_bottom = min(cap_bottom + 360, page.rect.height - 50)
    if boundary_y is not None:
        max_bottom = min(max_bottom, boundary_y - 8)
    rulings = _get_table_ruling_lines(page)
    aligned = [r for r in rulings if cap_bottom - 3 <= r.y0 <= max_bottom]
    if len(aligned) < 2:
        return None

    # 同页可能连续放置多个表格，只保留 caption 后的第一组横线。
    cluster = [aligned[0]]
    for ruling in aligned[1:]:
        if ruling.y0 - cluster[-1].y1 > 72:
            break
        cluster.append(ruling)
    if len(cluster) < 2:
        return None

    top = max(cap_bottom + 0.75, cluster[0].y0 - 0.75)
    bottom = cluster[-1].y1 + 8
    x0 = min(r.x0 for r in cluster) - 6
    x1 = max(r.x1 for r in cluster) + 6

    # 若横线下方紧跟 Note 行，一并纳入
    last_line_y = cluster[-1].y1
    for block in blocks:
        y0, y1 = block["bbox"][1], block["bbox"][3]
        text = block["text"].strip()
        if (
            y0 >= last_line_y - 4
            and y0 <= last_line_y + 25
            and text.startswith("Note")
            and (boundary_y is None or y1 < boundary_y)
        ):
            bottom = min(max_bottom, max(bottom, y1 + 8))
            break

    clip = fitz.Rect(x0, top, x1, bottom)
    if clip.height < 40 or clip.width < 80:
        return None
    return clip


def _table_clip_from_text(
    page,
    cap_bbox: tuple,
    blocks: list[dict],
    boundary_y: float | None = None,
):
    """横线不可用时的兜底：按文本块 + 段间距判断表格结束位置"""
    import fitz

    top = cap_bbox[3] + 2
    bottom = top
    prev_y1 = cap_bbox[3]

    for block in blocks:
        text = block["text"].strip()
        y0, y1 = block["bbox"][1], block["bbox"][3]
        if y1 <= cap_bbox[3] + 2:
            continue
        if boundary_y is not None and y0 >= boundary_y - 8:
            break

        gap = y0 - prev_y1
        if gap > 28:
            break
        if SECTION_HEADING_RE.match(text):
            break
        if PROSE_START_RE.match(text):
            break
        if gap > 18 and len(text) > 90 and not TABLE_ROW_HINT_RE.search(text):
            break

        bottom = y1 + 8
        prev_y1 = y1

    bottom = min(bottom, cap_bbox[3] + 360, page.rect.height - 50)
    if boundary_y is not None:
        bottom = min(bottom, boundary_y - 8)
    return fitz.Rect(54, top, page.rect.width - 54, bottom)


def _table_clip_rect(
    page,
    cap_bbox: tuple,
    blocks: list[dict],
    boundary_y: float | None = None,
):
    """Table：优先按矢量横线裁剪，否则按文本块推断"""
    clip = _table_clip_from_rulings(page, cap_bbox, blocks, boundary_y)
    if clip is not None:
        return clip
    return _table_clip_from_text(page, cap_bbox, blocks, boundary_y)


def _render_clip_to_file(page, clip, out_path: str, zoom: float = 2.0) -> bool:
    import fitz

    if clip.is_empty or clip.height < 60 or clip.width < 60:
        return False
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    pix.save(out_path)
    return os.path.getsize(out_path) > 3000


def _extract_pdf_figures(pdf_path: str, images_dir: str, output_dir: str) -> list[dict]:
    """
    按论文 Figure/Table 标题定位并渲染对应区域。
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        print("      未安装 pymupdf，跳过配图提取（pip install pymupdf）")
        return []

    figures: list[dict] = []
    os.makedirs(images_dir, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            captions = _find_captions_on_page(page, page_num)
            if not captions:
                continue

            blocks = _page_text_blocks(page)
            for cap_index, cap in enumerate(captions):
                next_caption_y = (
                    captions[cap_index + 1]["bbox"][1]
                    if cap_index + 1 < len(captions)
                    else None
                )
                if cap["kind"] == "figure":
                    clip = _figure_clip_rect(page, cap["bbox"])
                    fname = f"figure_{cap['num']}.png"
                else:
                    clip = _table_clip_rect(
                        page,
                        cap["bbox"],
                        blocks,
                        boundary_y=next_caption_y,
                    )
                    fname = f"table_{cap['num']}.png"

                out_path = os.path.join(images_dir, fname)
                if not _render_clip_to_file(page, clip, out_path):
                    print(f"      跳过无效裁剪: {cap['caption'][:50]}")
                    continue

                fig_id = f"fig{len(figures) + 1}"
                figures.append({
                    "id": fig_id,
                    "path": out_path,
                    "rel_path": os.path.relpath(out_path, output_dir),
                    "page": page_num,
                    "kind": cap["kind"],
                    "num": cap["num"],
                    "caption": cap["caption"],
                })
                print(f"      已渲染 {cap['kind']} {cap['num']} (p{page_num}) -> {fname}")
        doc.close()
    except Exception as e:
        print(f"      PDF 图表渲染失败: {e}")

    return figures


def _extract_pdf_text(pdf_path: str, max_chars: int = 12000) -> str:
    try:
        import fitz
    except ImportError:
        return ""

    text_parts = []
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            page_text = page.get_text("text").strip()
            if page_text:
                text_parts.append(page_text)
        doc.close()
    except Exception:
        return ""

    full_text = "\n\n".join(text_parts)
    if len(full_text) > max_chars:
        head = full_text[: int(max_chars * 0.7)]
        tail = full_text[-int(max_chars * 0.3):]
        full_text = head + "\n\n...[中间内容已截断]...\n\n" + tail
    return full_text


def _extract_pdf_content(
    pdf_path: str,
    images_dir: str,
    output_dir: str,
    max_chars: int = 12000,
) -> tuple[str, list[dict]]:
    """提取 PDF 正文 + 按 Figure/Table 标题渲染的配图"""
    pdf_text = _extract_pdf_text(pdf_path, max_chars)
    figures = _extract_pdf_figures(pdf_path, images_dir, output_dir)
    return pdf_text, figures


# ==================== 大模型总结（公众号风格） ====================
def _build_summary_prompt(
    paper: dict,
    pdf_text: str = "",
    inline_figures: list[dict] | None = None,
) -> str:
    authors = ", ".join(paper.get("authors", []))
    abstract = paper.get("abstract", "")
    search_query = paper.get("search_query", "")

    pdf_block = ""
    if pdf_text:
        pdf_block = f"""
## PDF 正文摘录（供参考）
{pdf_text}
"""

    figure_block = ""
    if inline_figures:
        lines = [
            "## 可用配图（已按论文 Figure/Table 标题从 PDF 精确裁剪渲染）",
            "请在对应小节通过 image_slot 引用，figure_caption 写中文图注：",
        ]
        for fig in inline_figures:
            kind_label = "图" if fig.get("kind") == "figure" else "表"
            lines.append(
                f"- {fig['id']} [{kind_label}, PDF第{fig['page']}页]: {fig.get('caption', '')}"
            )
        figure_block = "\n".join(lines)
    else:
        figure_block = "（未能从 PDF 识别 Figure/Table，正文不要引用图片）"

    return f"""你是一位资深 AI 学术公众号编辑（风格参考 PaperWeekly / 机器之心），要把英文论文改写成**适合微信公众号发布**的中文推文。

## 写作风格（非常重要）
1. **标题要吸睛**：18-30 字，可含机构/方法/亮点/数字/问号，避免照搬英文论文标题
   - 好例子：「低资源语言 RAG 怎么切分才靠谱？四种策略系统对比给出答案」
   - 好例子：「CMU 提出 NLP 新范式，高考英语交出 134 高分」
   - 差例子：「Evaluation of Chunking Strategies for...」（太学术、太长）
2. **语言口语化但专业**：像给同行朋友讲论文，可用「简单来说」「值得注意的是」「划重点」
3. **段落要短**：每段 2-4 句，适合手机阅读
4. **数字要突出**：关键指标、提升幅度写清楚
5. **不要编造**：所有结论必须来自给定材料
6. **表达自然**：避免“不是……而是……”“真正实现”“赋能”“值得注意的是”等模板化句式，
   少用口号和夸张判断，优先直接说明事实、方法和结果

## 检索背景
{search_query or "未提供"}

## 论文元数据
- 英文标题: {paper.get("title", "")}
- 作者: {authors}
- arXiv: {paper.get("arxiv_id", "")}
- 发表: {paper.get("published", "")}

## 摘要
{abstract}
{pdf_block}

{figure_block}

## 输出格式
请**只输出 JSON**（不要 markdown 代码块），结构如下：

{{
  "wechat_title": "公众号吸睛标题（18-30字）",
  "wechat_subtitle": "副标题，一句话补充亮点（可选，15-25字）",
  "lead": "导语，2-3句，用悬念或问题引入，适合放在 blockquote",
  "sections": [
    {{
      "title": "小节标题（如：为什么要关注这篇论文？/ 核心方法 / 实验结果有多强？/ 划重点）",
      "content": "正文，Markdown 格式，可用 **加粗** 和列表，段落之间用 \\n\\n",
      "image_slot": null,
      "figure_caption": ""
    }}
  ],
  "closing": "结尾 1-2 段：总结价值 + 鼓励读者去看原文"
}}

要求：
- sections 至少 4 个小节，顺序建议：背景引入 → 方法 → 实验结果 → 划重点
- **fig1 通常是 Figure 1（方法/流程图），放在「核心方法」**；**含 Table 的 fig 放在「实验结果」**
- 至少使用 1 张配图；若有 Table 2（结果对比表），务必插入
- figure_caption 用中文，格式如「图1：端到端分块评估流程」「表2：四种策略性能对比」
- content 里不要重复输出 wechat_title
"""


def _parse_llm_json(content: str) -> dict | None:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _llm_generate_article(
    paper: dict,
    client: OpenAI,
    pdf_text: str = "",
    inline_figures: list[dict] | None = None,
) -> dict | None:
    prompt = _build_summary_prompt(paper, pdf_text, inline_figures)
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.45,
            max_tokens=4096,
        )
        content = response.choices[0].message.content.strip()
        return _parse_llm_json(content)
    except Exception as e:
        print(f"      大模型调用失败: {e}")
        return None


def _figure_lookup(inline_figures: list[dict]) -> dict[str, dict]:
    return {fig["id"]: fig for fig in inline_figures}


def _assemble_wechat_body(
    article: dict,
    paper: dict,
    cover_rel: str,
    inline_figures: list[dict],
) -> str:
    """将 LLM JSON 组装为公众号风格 Markdown 正文"""
    fig_map = _figure_lookup(inline_figures)
    lines: list[str] = []

    title = article.get("wechat_title", paper.get("title", "论文分享"))
    subtitle = article.get("wechat_subtitle", "").strip()
    lead = article.get("lead", "").strip()

    lines.append(f"# {title}\n")
    if subtitle:
        lines.append(f"### {subtitle}\n")

    if cover_rel:
        lines.append(f"![封面]({cover_rel})\n")
    else:
        lines.append("<!-- 封面图：待模块4（封面图生成）补充 -->\n")

    if lead:
        lines.append(f"> {lead}\n")

    authors = ", ".join(paper.get("authors", [])[:5])
    if len(paper.get("authors", [])) > 5:
        authors += " 等"
    arxiv_id = paper.get("arxiv_id", "")
    entry_id = paper.get("entry_id", f"https://arxiv.org/abs/{arxiv_id}")
    lines.append(f"**论文**：{paper.get('title', '')}\n")
    lines.append(f"**作者**：{authors}\n")
    lines.append(f"**来源**：[arXiv:{arxiv_id}]({entry_id})\n")
    lines.append("\n")
    lines.append("---\n")

    for section in article.get("sections", []):
        sec_title = section.get("title", "").strip()
        sec_content = section.get("content", "").strip()
        if sec_title:
            lines.append(f"## {sec_title}\n")
        if sec_content:
            lines.append(f"{sec_content}\n")

        slot = section.get("image_slot")
        caption = section.get("figure_caption", "").strip()
        if slot and slot in fig_map:
            rel = fig_map[slot]["rel_path"]
            alt = caption or fig_map[slot].get("caption", f"论文配图 {slot}")
            lines.append(f"![{alt}]({rel})\n")
            if caption:
                lines.append(f"*{caption}*\n")
            elif fig_map[slot].get("caption"):
                lines.append(f"*{fig_map[slot]['caption']}*\n")

    closing = article.get("closing", "").strip()
    if closing:
        lines.append("## 写在最后\n")
        lines.append(f"{closing}\n")

    pdf_url = paper.get("pdf_url", "")
    lines.append("---\n")
    lines.append(f"📄 **阅读原文**：[PDF 下载]({pdf_url}) | [arXiv 页面]({entry_id})\n")

    return "".join(lines)


def _build_markdown_document(
    paper: dict,
    article: dict,
    body: str,
    source_json: str,
    cover_rel: str,
    inline_figures: list[dict],
    output_dir: str,
) -> str:
    authors_yaml = json.dumps(paper.get("authors", []), ensure_ascii=False)
    wechat_title = article.get("wechat_title", paper.get("title", ""))
    inline_yaml = json.dumps(
        [{"id": f["id"], "path": f["rel_path"], "caption": f.get("caption", ""), "kind": f.get("kind", "")} for f in inline_figures],
        ensure_ascii=False,
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    front_matter = f"""---
wechat_title: {json.dumps(wechat_title, ensure_ascii=False)}
paper_title: {json.dumps(paper.get("title", ""), ensure_ascii=False)}
arxiv_id: {paper.get("arxiv_id", "")}
authors: {authors_yaml}
published: {paper.get("published", "")}
pdf_url: {paper.get("pdf_url", "")}
entry_id: {paper.get("entry_id", "")}
search_query: {json.dumps(paper.get("search_query", ""), ensure_ascii=False)}
cover_image: {json.dumps(cover_rel, ensure_ascii=False)}
inline_images: {inline_yaml}
source_json: {json.dumps(source_json, ensure_ascii=False)}
generated_at: {generated_at}
module: paper_summarizer
format: wechat_article
---

"""
    return front_matter + body + "\n"


def _save_summary_markdown(content: str, paper: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    arxiv_id = paper.get("arxiv_id", "unknown")
    safe_id = arxiv_id.replace(".", "_") if arxiv_id else "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_id}_{timestamp}.md"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def _update_summary_index(output_dir: str, index: dict[str, str]):
    """生成 summaries/index.md 索引"""
    index_path = os.path.join(output_dir, "index.md")
    entries = []

    for arxiv_id, rel_path in sorted(index.items()):
        md_path = os.path.join(output_dir, rel_path)
        title = arxiv_id
        meta = {}
        if os.path.exists(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    text = f.read()
                fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
                if fm_match:
                    for line in fm_match.group(1).splitlines():
                        if line.startswith("wechat_title:"):
                            title = json.loads(line.split(":", 1)[1].strip())
                        elif line.startswith("title:"):
                            title = json.loads(line.split(":", 1)[1].strip())
                        elif line.startswith("pdf_url:"):
                            meta["pdf_url"] = line.split(":", 1)[1].strip()
                        elif line.startswith("generated_at:"):
                            meta["generated_at"] = line.split(":", 1)[1].strip()
            except Exception:
                pass
        entries.append({"arxiv_id": arxiv_id, "title": title, "path": rel_path, **meta})

    lines = [
        "# 论文 Markdown 总结索引\n",
        "",
        f"共 {len(entries)} 篇 | 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "",
    ]
    for i, e in enumerate(entries, 1):
        lines.append(f"### {i}. {e['title']}\n")
        lines.append(f"- **arXiv ID**: {e['arxiv_id']}\n")
        if e.get("pdf_url"):
            lines.append(f"- **PDF**: {e['pdf_url']}\n")
        lines.append(f"- **总结文件**: `{e['path']}`\n")
        if e.get("generated_at"):
            lines.append(f"- **生成时间**: {e['generated_at']}\n")
        lines.append("\n")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print(f"    index.md 已更新: {index_path}")


# ==================== 单篇处理 ====================
def summarize_paper(
    paper_json_path: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    use_pdf: bool = True,
    client: OpenAI | None = None,
    force: bool = False,
) -> str | None:
    """
    总结单篇论文，返回生成的 Markdown 路径；失败返回 None。
    use_pdf=True 时会下载 PDF 提取封面/配图（公众号排版必需）。
    """
    paper = _load_paper_json(paper_json_path)
    paper = _enrich_paper_metadata(paper)
    arxiv_id = paper.get("arxiv_id", "")

    if not arxiv_id:
        print(f"  跳过：无法解析 arXiv ID -> {paper_json_path}")
        return None

    index = _load_summarized_index(output_dir)
    if not force and arxiv_id in index:
        existing = os.path.join(output_dir, index[arxiv_id])
        print(f"  已总结过，跳过: {arxiv_id} -> {existing}")
        return existing

    llm_client = client or OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    pdf_text = ""
    cover_rel = ""
    inline_figures: list[dict] = []

    if use_pdf and paper.get("pdf_url"):
        pdf_dir = os.path.join(output_dir, "pdfs")
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_path = os.path.join(pdf_dir, f"{arxiv_id.replace('.', '_')}.pdf")
        images_dir = os.path.join(output_dir, "images", arxiv_id.replace(".", "_"))

        if not os.path.exists(pdf_path):
            print(f"      正在下载 PDF...")
            if not _download_pdf(paper["pdf_url"], pdf_path):
                pdf_path = ""
        else:
            print(f"      使用已缓存 PDF")

        if pdf_path and os.path.exists(pdf_path):
            print(f"      正在识别 Figure/Table 并渲染配图...")
            pdf_text, inline_figures = _extract_pdf_content(pdf_path, images_dir, output_dir)
            if inline_figures:
                print(f"      共渲染 {len(inline_figures)} 张论文图表（封面待模块4生成）")
            else:
                print(f"      未能识别论文 Figure/Table")

    print(f"      正在调用大模型生成公众号推文...")
    article = _llm_generate_article(
        paper,
        llm_client,
        pdf_text,
        inline_figures,
    )
    if not article:
        return None

    body = _assemble_wechat_body(article, paper, cover_rel, inline_figures)
    source_json = os.path.relpath(paper_json_path, os.path.dirname(os.path.abspath(__file__)))
    md_content = _build_markdown_document(
        paper, article, body, source_json, cover_rel, inline_figures, output_dir,
    )
    filepath = _save_summary_markdown(md_content, paper, output_dir)

    rel_path = os.path.relpath(filepath, output_dir)
    index[arxiv_id] = rel_path
    _save_summarized_index(output_dir, index)
    _update_summary_index(output_dir, index)

    return filepath


# ==================== 批量流水线 ====================
def run_pipeline(
    papers_dir: str = DEFAULT_PAPERS_DIR,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    use_pdf: bool = True,
    max_papers: int = 10,
    force: bool = False,
) -> list[str]:
    """
    批量总结 papers/ 目录下尚未处理的论文 JSON。

    参数:
        papers_dir: 模块1输出的论文 JSON 目录
        output_dir: Markdown 输出目录
        use_pdf: 是否下载 PDF 提取封面/配图（默认开启，公众号排版推荐）
        max_papers: 单次最多处理篇数（控制大模型调用次数）
        force: 是否强制重新总结已处理过的论文

    返回:
        生成的 Markdown 文件路径列表
    """
    paper_files = _list_paper_json_files(papers_dir)
    index = _load_summarized_index(output_dir)
    llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    pending = []
    for fp in paper_files:
        try:
            paper = _load_paper_json(fp)
            arxiv_id = _resolve_arxiv_id(paper)
            if not arxiv_id:
                continue
            if not force and arxiv_id in index:
                continue
            pending.append(fp)
        except Exception:
            continue

    print("=" * 60)
    print("  论文 → Markdown 总结流水线 (模块2)")
    print(f"  输入目录: {papers_dir}")
    print(f"  输出目录: {output_dir}")
    print(f"  待处理: {len(pending)} 篇 | 单次上限: {max_papers} 篇")
    print(f"  PDF 配图: {'按 Figure/Table 渲染' if use_pdf else '关闭'}")
    print("=" * 60)

    generated = []
    for i, fp in enumerate(pending[:max_papers], 1):
        paper = _load_paper_json(fp)
        arxiv_id = _resolve_arxiv_id(paper)
        title = paper.get("title", "")[:60]
        print(f"\n[{i}/{min(len(pending), max_papers)}] {arxiv_id} - {title}...")

        result = summarize_paper(
            fp,
            output_dir=output_dir,
            use_pdf=use_pdf,
            client=llm_client,
            force=force,
        )
        if result:
            generated.append(result)
            print(f"      已保存: {result}")

    if not pending:
        print("\n  没有待处理的论文（全部已总结或未找到 JSON）")
    elif not generated:
        print("\n  未成功生成任何总结")

    print("\n" + "=" * 60)
    print(f"  完成！本次生成 {len(generated)} 篇")
    if generated:
        for f in generated:
            print(f"    - {os.path.basename(f)}")
    print("=" * 60)

    return generated
