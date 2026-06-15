# -*- coding: utf-8 -*-
"""
Module 4: cover image generation for WeChat articles.

Strategy:
  1. Detect paper theme and extract accurate copy from Markdown (titles, subtitle, tags)
  2. Generate paper-relevant background via AI (no text in the generated pixels)
  3. Overlay Chinese/English text with Pillow so wording matches the article exactly
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

MODULE4_DIR = Path(__file__).resolve().parent
GET_DATA_DIR = MODULE4_DIR.parent
if str(GET_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(GET_DATA_DIR))


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_module_env() -> None:
    """Load env.bundle (shipped with package) then optional local .env overrides."""
    _load_env_file(MODULE4_DIR / "env.bundle")
    _load_env_file(MODULE4_DIR / ".env")


_load_module_env()

from md_to_wechat_core import list_markdown_files, parse_front_matter  # noqa: E402

DEFAULT_SUMMARIES_DIR = GET_DATA_DIR / "summaries"
COVERS_INDEX_PATH = MODULE4_DIR / "covers.json"

COVER_WIDTH = 900
COVER_HEIGHT = 500
SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/images/generations"
SILICONFLOW_DEFAULT_MODEL = "Tongyi-MAI/Z-Image-Turbo"

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_IMAGE_MODEL = os.getenv("SILICONFLOW_IMAGE_MODEL", SILICONFLOW_DEFAULT_MODEL)

PLACEHOLDER_PATTERN = re.compile(
    r"<!--\s*\u5c01\u9762\u56fe\uff1a\u5f85\u6a21\u57574.*?\u8865\u5145\s*-->"
)


@dataclass
class CoverTheme:
    name: str
    bg_top: tuple[int, int, int]
    bg_bottom: tuple[int, int, int]
    accent: tuple[int, int, int]
    accent2: tuple[int, int, int]
    keywords: list[str] = field(default_factory=list)
    image_prompt: str = ""
    subtitle: str = ""


@dataclass
class CoverCopy:
    chinese_title: str
    english_title: str
    chinese_subtitle: str
    keywords: list[str]


THEMES: dict[str, CoverTheme] = {
    "finance": CoverTheme(
        name="finance",
        bg_top=(8, 20, 48),
        bg_bottom=(18, 52, 98),
        accent=(212, 175, 55),
        accent2=(96, 165, 250),
        keywords=["Quant Finance", "Yield Curve", "Deep Learning"],
        image_prompt=(
            "Abstract financial data visualization, glowing yield curves and volatility surface, "
            "neural network nodes merged with candlestick charts, dark navy and gold palette, "
            "cinematic lighting, no text, no letters, ultra clean, 16:9"
        ),
    ),
    "nlp": CoverTheme(
        name="nlp",
        bg_top=(18, 12, 56),
        bg_bottom=(52, 28, 110),
        accent=(129, 140, 248),
        accent2=(56, 189, 248),
        keywords=["NLP", "RAG", "Embeddings"],
        image_prompt=(
            "Abstract RAG and embedding concept art, glowing document chunks connected to "
            "neural network nodes, vector retrieval graph, purple and cyan gradient, "
            "futuristic data pipeline visualization, symbolic blocks not readable text"
        ),
    ),
    "geo": CoverTheme(
        name="geo",
        bg_top=(10, 38, 52),
        bg_bottom=(20, 78, 92),
        accent=(45, 212, 191),
        accent2=(251, 191, 36),
        keywords=["Geospatial", "QA Benchmark", "GIS"],
        image_prompt=(
            "Abstract geospatial intelligence scene, digital map layers, satellite grid and location pins, "
            "teal and amber highlights on dark background, no text, no letters, 16:9"
        ),
    ),
    "general": CoverTheme(
        name="general",
        bg_top=(15, 23, 42),
        bg_bottom=(30, 41, 59),
        accent=(99, 102, 241),
        accent2=(236, 72, 153),
        keywords=["AI Research", "Paper Review"],
        image_prompt=(
            "Abstract scientific research hero image, neural network and data streams, "
            "deep blue magenta gradient, modern tech editorial style, no text, no letters, 16:9"
        ),
    ),
}


def _arxiv_dir(arxiv_id: str) -> str:
    return arxiv_id.replace(".", "_")


def _cover_rel_path(arxiv_id: str) -> str:
    return f"images/{_arxiv_dir(arxiv_id)}/cover.png"


def _load_covers_index() -> dict[str, Any]:
    if not COVERS_INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(COVERS_INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_covers_index(index: dict[str, Any]) -> None:
    COVERS_INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _pick_font(size: int, bold: bool = False):
    from PIL import ImageFont

    bold_candidates = [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    regular_candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    candidates = bold_candidates + regular_candidates if bold else regular_candidates + bold_candidates
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _metadata_text(metadata: dict[str, Any]) -> str:
    parts = [
        str(metadata.get("wechat_title") or ""),
        str(metadata.get("paper_title") or ""),
        str(metadata.get("search_query") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def detect_theme(metadata: dict[str, Any]) -> CoverTheme:
    text = _metadata_text(metadata)
    if any(k in text for k in ("yield", "volatility", "finance", "curve", "vae", "arbitrage", "swap", "option")):
        return THEMES["finance"]
    if any(k in text for k in ("rag", "retrieval", "embedding", "llm", "chunk", "language", "nlp")):
        return THEMES["nlp"]
    if any(k in text for k in ("geo", "spatial", "gis", "map", "location")):
        return THEMES["geo"]
    return THEMES["general"]


def _extract_keywords_heuristic(
    metadata: dict[str, Any],
    theme: CoverTheme,
    body: str = "",
) -> list[str]:
    title = str(metadata.get("wechat_title") or metadata.get("paper_title") or "")
    combined = f"{title} {metadata.get('paper_title', '')} {body[:800]}"
    found: list[str] = []
    for token in re.findall(
        r"\b(RAG|LLM|VAE|GAN|Transformer|SDE|CNN|FAISS|BGE|NLP|GIS|QA)\b",
        combined,
        flags=re.IGNORECASE,
    ):
        label = token.upper()
        if label not in found:
            found.append(label)
    cn_rules = [
        ("\u5207\u5757", "\u5207\u5757"),
        ("\u5206\u5757", "\u5206\u5757"),
        ("\u9012\u5f52", "\u9012\u5f52\u6cd5"),
        ("\u9ad8\u68c9", "\u9ad8\u68c9\u8bed"),
        ("\u4f4e\u8d44\u6e90", "\u4f4e\u8d44\u6e90\u8bed\u8a00"),
        ("\u519c\u4e1a", "\u519c\u4e1a\u6587\u6863"),
        ("\u6536\u76ca\u7387", "\u6536\u76ca\u7387"),
        ("\u6ce2\u52a8\u7387", "\u6ce2\u52a8\u7387"),
        ("\u66f2\u7ebf", "\u66f2\u7ebf"),
        ("\u65e0\u5957\u5229", "\u65e0\u5957\u5229"),
        ("\u5d4c\u5165", "\u5d4c\u5165"),
        ("\u5730\u7406", "\u5730\u7406"),
        ("\u95ee\u7b54", "\u95ee\u7b54"),
    ]
    for pattern, label in cn_rules:
        if pattern in combined and label not in found:
            found.append(label)
    if not found:
        found = list(theme.keywords[:3])
    return found[:4]


def _extract_cover_copy(metadata: dict[str, Any], body: str = "") -> CoverCopy:
    """Extract overlay text directly from front matter and article body."""
    chinese_title = str(
        metadata.get("wechat_title") or metadata.get("paper_title") or "\u5b66\u672f\u8bba\u6587"
    ).strip()
    english_title = str(metadata.get("paper_title") or "").strip()

    subtitle_match = re.search(r"^###\s+(.+)$", body, re.MULTILINE)
    chinese_subtitle = subtitle_match.group(1).strip() if subtitle_match else ""

    theme = detect_theme(metadata)
    keywords = _extract_keywords_heuristic(metadata, theme, body)

    return CoverCopy(
        chinese_title=chinese_title,
        english_title=english_title,
        chinese_subtitle=chinese_subtitle,
        keywords=keywords,
    )


def _article_text_blob(metadata: dict[str, Any], body_excerpt: str = "") -> str:
    parts = [
        str(metadata.get("wechat_title") or ""),
        str(metadata.get("paper_title") or ""),
        str(metadata.get("search_query") or ""),
        body_excerpt,
    ]
    return " ".join(p for p in parts if p)


def _extract_visual_hints(metadata: dict[str, Any], body_excerpt: str = "") -> list[str]:
    """Turn article content into text-safe English visual metaphors."""
    text = _article_text_blob(metadata, body_excerpt).lower()
    hints: list[str] = []

    if any(k in text for k in ("khmer", "\u9ad8\u68c9", "cambod", "low-resource", "\u4f4e\u8d44\u6e90")):
        hints.append(
            "Cambodian Southeast Asia low-resource language research atmosphere, "
            "tropical countryside mood, no writing or script marks anywhere"
        )
    if any(k in text for k in ("agricultur", "\u519c\u4e1a", "farm", "crop", "rice", "paddy")):
        hints.append(
            "photorealistic terraced rice paddies and farmland as the dominant scenic background, "
            "golden hour natural lighting"
        )
    if any(k in text for k in ("chunk", "\u5207\u5757", "\u5206\u5757")):
        hints.append(
            "subtle translucent overlay of blank paper shards split into different-sized strips, "
            "pure color blocks with no markings inside"
        )
    if any(k in text for k in ("recursive", "\u9012\u5f52")):
        hints.append(
            "one golden luminous stream slightly brighter than the others, symbolizing the best method"
        )
    if any(k in text for k in ("\u56db\u79cd", "four")) and any(
        k in text for k in ("strateg", "\u7b56\u7565", "\u5206\u5757", "chunk")
    ):
        hints.append(
            "four flowing light trails in purple gold blue and red merging toward center, "
            "organic curves not rectangular UI cards"
        )
    if any(k in text for k in ("embedding", "\u5d4c\u5165", "bge", "faiss", "vector", "\u5411\u91cf")):
        hints.append(
            "sparse constellation of glowing particles and thin connection lines, "
            "abstract vector space metaphor without icons or labels"
        )
    if any(k in text for k in ("rag", "retrieval", "\u68c0\u7d22")):
        hints.append(
            "soft holographic data mist linking scattered fragments to a central bright orb, "
            "minimal sci-fi overlay on the landscape"
        )
    if any(k in text for k in ("sentence", "\u53e5\u5b50")):
        hints.append("one fragmented pale-blue stream with many tiny pieces versus fewer larger pieces")
    if any(k in text for k in ("llm", "\u5927\u6a21\u578b")) and any(
        k in text for k in ("chunk", "\u5206\u5757")
    ):
        hints.append("a faint neural glow motif, subtle and secondary to the landscape")

    if any(k in text for k in ("yield", "\u6536\u76ca\u7387", "curve", "\u66f2\u7ebf")):
        hints.append(
            "glowing 3D treasury yield curve surface with term structure, bond maturity axis, "
            "financial econometrics atmosphere"
        )
    if any(k in text for k in ("volatility", "\u6ce2\u52a8\u7387", "option")):
        hints.append("implied volatility smile surface mesh overlaid on the curve")
    if any(k in text for k in ("vae", "variational", "\u65e0\u5957\u5229", "arbitrage")):
        hints.append(
            "variational autoencoder latent manifold merging with arbitrage-free constraint geometry, "
            "no-arbitrage financial modeling aesthetic"
        )
    if any(k in text for k in ("geo", "spatial", "gis", "\u5730\u7406", "map")):
        hints.append("digital geospatial map layers, satellite grid, location pins on terrain")

    return hints


def _llm_cover_brief(metadata: dict[str, Any], body_excerpt: str = "") -> dict[str, Any] | None:
    if not LLM_API_KEY:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    wechat_title = str(metadata.get("wechat_title") or "")
    paper_title = str(metadata.get("paper_title") or "")
    search_query = str(metadata.get("search_query") or "")
    arxiv_id = str(metadata.get("arxiv_id") or "")

    heuristic_hints = _extract_visual_hints(metadata, body_excerpt)
    prompt = (
        "You design thematic cover backgrounds for Chinese science articles on WeChat.\n"
        "Return JSON for theme/keywords/subtitle only. image_prompt is optional and rarely used.\n"
        "If you write image_prompt: use cinematic scenery plus subtle abstract light effects. "
        "NEVER mention flowcharts, infographics, boxes, panels, UI, brains, scripts, or acronyms. "
        "NEVER any readable text in the image.\n"
        "Return JSON only:\n"
        "{\n"
        '  "theme": "finance|nlp|geo|general",\n'
        '  "subtitle": "Chinese subtitle, 12-20 chars",\n'
        '  "keywords": ["2-4 short Chinese/English tags"],\n'
        '  "image_prompt": "optional, English, scenery-first, no text, no UI"\n'
        "}\n\n"
        f"Chinese title: {wechat_title}\n"
        f"English title: {paper_title}\n"
        f"Research domain: {search_query}\n"
        f"arXiv: {arxiv_id}\n"
        f"Article excerpt: {body_excerpt[:600]}\n"
        f"Heuristic visual hints (incorporate and enrich): {', '.join(heuristic_hints[:8])}"
    )

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=400,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"      LLM cover brief failed: {exc}")
        return None


def enrich_cover_context(
    metadata: dict[str, Any],
    body_excerpt: str = "",
    body: str = "",
) -> CoverTheme:
    theme = detect_theme(metadata)
    brief = _llm_cover_brief(metadata, body_excerpt)
    if not brief:
        return CoverTheme(
            name=theme.name,
            bg_top=theme.bg_top,
            bg_bottom=theme.bg_bottom,
            accent=theme.accent,
            accent2=theme.accent2,
            keywords=_extract_keywords_heuristic(metadata, theme, body or body_excerpt),
            image_prompt=build_image_prompt(metadata, theme, body_excerpt),
            subtitle=str(metadata.get("search_query") or "")[:24],
        )

    theme_name = str(brief.get("theme") or theme.name)
    base = THEMES.get(theme_name, theme)
    keywords = brief.get("keywords") or _extract_keywords_heuristic(
        metadata, base, body or body_excerpt
    )
    if isinstance(keywords, str):
        keywords = [keywords]
    return CoverTheme(
        name=base.name,
        bg_top=base.bg_top,
        bg_bottom=base.bg_bottom,
        accent=base.accent,
        accent2=base.accent2,
        keywords=[str(k) for k in keywords][:4],
        image_prompt=build_image_prompt(metadata, base, body_excerpt),
        subtitle=str(brief.get("subtitle") or "")[:30],
    )


PURE_IMAGE_SUFFIX = (
    "ABSOLUTELY NO text, NO letters, NO words, NO numbers, NO symbols resembling writing, "
    "NO labels, NO captions, NO watermark, NO logo, NO UI, NO typography, NO infographics, "
    "NO flowchart, NO rectangular cards, NO boxes with content inside. "
    "Photorealistic or painterly scenery with minimal abstract overlay, 16:9 cover background."
)

PURE_IMAGE_NEGATIVE = (
    "text, letters, words, numbers, typography, label, caption, title, subtitle, "
    "watermark, logo, arxiv, banner, badge, chinese characters, english text, khmer script, "
    "thai script, alphabet, writing, calligraphy, sign, plaque, nameplate, interface panel, "
    "infographic, flowchart, diagram, rectangular box, card UI, pseudo-text, garbled letters, "
    "misspelling, blurry, low quality, cluttered, brain illustration"
)


def build_image_prompt(
    metadata: dict[str, Any],
    theme: CoverTheme | None = None,
    body_excerpt: str = "",
) -> str:
    """Visual-only English prompt derived from article content, not paper titles."""
    theme = theme or detect_theme(metadata)
    hints = _extract_visual_hints(metadata, body_excerpt)
    if hints:
        palette = {
            "finance": "deep navy and gold financial terminal palette",
            "nlp": "purple cyan and indigo AI research palette",
            "geo": "teal amber geospatial palette",
            "general": "deep blue magenta scientific palette",
        }[theme.name]
        base = (
            "Cinematic editorial cover photo for a science article, topic-specific mood. "
            + ". ".join(hints[:7])
            + f". {palette}, landscape-first composition, subtle holographic accents only, "
            "clean uncluttered frame, magazine hero image"
        )
    else:
        search_query = str(metadata.get("search_query") or "").strip()
        base = theme.image_prompt or (
            f"Editorial tech magazine cover background, {theme.name} research theme, "
            "cinematic lighting, modern scientific illustration"
        )
        if search_query:
            base = f"{base} Visual mood inspired by: {search_query}."
    return f"{base} {PURE_IMAGE_SUFFIX}"


def _text_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


MIXED_LINE_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+\-]*"
    r"|[0-9]+(?:\.[0-9]+)?[A-Za-z%]*"
    r"|[\u4e00-\u9fff]"
    r"|[\u3001\u3002\uff0c\uff1a\uff1b\uff1f\uff01,.:;!?]"
)


def _tokenize_mixed_line(text: str) -> list[str]:
    """Split mixed Chinese/English title into tokens that must not break across lines."""
    return MIXED_LINE_TOKEN_RE.findall(text)


def _wrap_text_lines_words(text: str, font, draw, max_width: int, max_lines: int = 2) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _text_width(draw, trial, font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
    if len(lines) < max_lines and current:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines)) < len(text):
        last = lines[-1]
        ellipsis = "..."
        while _text_width(draw, last + ellipsis, font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + ellipsis
    return lines


def _wrap_text_lines(
    text: str,
    font,
    draw,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    """Wrap mixed Chinese/English text without splitting English tokens like VAE or 6.58bps."""
    tokens = _tokenize_mixed_line(text)
    if not tokens:
        return []

    unlimited = max_lines is None or max_lines <= 0
    lines: list[str] = []
    current = ""
    consumed = 0
    for token in tokens:
        consumed += 1
        trial = current + token
        if not current or _text_width(draw, trial, font) <= max_width:
            current = trial
            continue

        lines.append(current)
        if not unlimited and len(lines) >= max_lines:
            current = ""
            break
        current = token

    if current and (unlimited or len(lines) < max_lines):
        lines.append(current)

    if not unlimited and lines and consumed < len(tokens):
        last = lines[-1]
        ellipsis = "\u2026"
        while _text_width(draw, last + ellipsis, font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + ellipsis
    return lines


def _fit_title_layout(text: str, draw, max_width: int = 540) -> tuple[Any, list[str], int]:
    """Pick title font size/line height so long Chinese titles can use more than 3 lines."""
    for size, line_h in ((36, 46), (32, 40), (28, 34)):
        font = _pick_font(size, bold=True)
        lines = _wrap_text_lines(text, font, draw, max_width, max_lines=None)
        if len(lines) <= 4 or size == 28:
            return font, lines, line_h
    return font, lines, line_h


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]):
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / max(h - 1, 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)
    return img


def _draw_finance_art(draw, w: int, h: int, accent: tuple[int, int, int], accent2: tuple[int, int, int]) -> None:
    random.seed(42)
    for idx, base_y in enumerate((170, 230, 300)):
        points: list[tuple[int, int]] = []
        for x in range(360, w - 20, 8):
            t = (x - 360) / max(w - 380, 1)
            y = int(base_y + 35 * math.sin(t * 4 + idx) + 25 * t + 12 * math.cos(t * 7))
            points.append((x, y))
        color = accent if idx == 0 else accent2
        draw.line(points, fill=color, width=3 if idx == 0 else 2)
    for x, y, r in ((720, 120, 70), (820, 360, 50)):
        draw.ellipse((x - r, y - r, x + r, y + r), outline=accent, width=2)


def _draw_nlp_art(draw, w: int, h: int, accent: tuple[int, int, int], accent2: tuple[int, int, int]) -> None:
    nodes = [(520, 120), (660, 90), (780, 150), (600, 240), (740, 260), (840, 200), (700, 340)]
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 260:
                draw.line([a, b], fill=(accent[0] // 2, accent[1] // 2, accent[2] // 2), width=1)
    for x, y in nodes:
        draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=accent2, outline=accent, width=2)


def _draw_geo_art(draw, w: int, h: int, accent: tuple[int, int, int], accent2: tuple[int, int, int]) -> None:
    for gx in range(420, w, 48):
        draw.line([(gx, 60), (gx, h - 40)], fill=(accent[0] // 3, accent[1] // 3, accent[2] // 3), width=1)
    for gy in range(60, h - 20, 48):
        draw.line([(420, gy), (w - 20, gy)], fill=(accent[0] // 3, accent[1] // 3, accent[2] // 3), width=1)
    for x, y in ((560, 180), (700, 260), (820, 150)):
        draw.ellipse((x - 10, y - 18, x + 10, y + 2), fill=accent2)
        draw.line([(x, y + 2), (x, y + 28)], fill=accent, width=3)


def _draw_theme_art(draw, theme: CoverTheme, w: int, h: int) -> None:
    if theme.name == "finance":
        _draw_finance_art(draw, w, h, theme.accent, theme.accent2)
    elif theme.name == "nlp":
        _draw_nlp_art(draw, w, h, theme.accent, theme.accent2)
    elif theme.name == "geo":
        _draw_geo_art(draw, w, h, theme.accent, theme.accent2)
    else:
        for r, alpha in ((180, 30), (120, 20), (90, 15)):
            x, y = w - 180, 120
            draw.ellipse((x - r, y - r, x + r, y + r), outline=theme.accent, width=2)


def create_theme_background(theme: CoverTheme):
    from PIL import Image, ImageDraw

    img = _vertical_gradient((COVER_WIDTH, COVER_HEIGHT), theme.bg_top, theme.bg_bottom)
    draw = ImageDraw.Draw(img)
    _draw_theme_art(draw, theme, COVER_WIDTH, COVER_HEIGHT)
    return img


def _fetch_pollinations_image(prompt: str, timeout: int = 120):
    from PIL import Image

    encoded = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={COVER_WIDTH}&height={COVER_HEIGHT}&nologo=true&seed={abs(hash(prompt)) % 10000}"
    )
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


def _fetch_siliconflow_image(
    prompt: str,
    model: str | None = None,
    timeout: int = 180,
):
    from PIL import Image

    key = SILICONFLOW_API_KEY or os.getenv("SILICONFLOW_API_KEY", "")
    if not key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY")

    model_name = model or SILICONFLOW_IMAGE_MODEL or SILICONFLOW_DEFAULT_MODEL
    payload: dict[str, Any] = {
        "model": model_name,
        "prompt": prompt,
        "image_size": "1024x576",
        "batch_size": 1,
        "negative_prompt": PURE_IMAGE_NEGATIVE,
    }
    if "Kolors" in model_name:
        payload["guidance_scale"] = 7.5
        payload["num_inference_steps"] = 20
    elif "FLUX" in model_name:
        payload["num_inference_steps"] = 4

    resp = requests.post(
        SILICONFLOW_API_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"SiliconFlow HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    images = data.get("images") or data.get("data") or []
    if not images:
        raise RuntimeError(f"SiliconFlow empty response: {data}")

    image_url = images[0].get("url") or images[0].get("b64_json")
    if not image_url:
        raise RuntimeError(f"SiliconFlow missing image url: {images[0]}")

    if str(image_url).startswith("http"):
        img_resp = requests.get(image_url, timeout=timeout)
        img_resp.raise_for_status()
        content = img_resp.content
    else:
        import base64

        content = base64.b64decode(image_url)

    return Image.open(BytesIO(content)).convert("RGB"), model_name


def _fetch_dashscope_image(prompt: str, timeout: int = 120):
    from PIL import Image

    key = os.getenv("DASHSCOPE_API_KEY", "")
    if not key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": "wanx-v1",
        "input": {"prompt": prompt},
        "parameters": {"size": f"{COVER_WIDTH}*{COVER_HEIGHT}", "n": 1},
    }
    create_resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    create_resp.raise_for_status()
    task_id = create_resp.json()["output"]["task_id"]
    task_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    for _ in range(60):
        task_resp = requests.get(task_url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        task_resp.raise_for_status()
        data = task_resp.json()
        status = data.get("output", {}).get("task_status")
        if status == "SUCCEEDED":
            image_url = data["output"]["results"][0]["url"]
            img_resp = requests.get(image_url, timeout=timeout)
            img_resp.raise_for_status()
            return Image.open(BytesIO(img_resp.content)).convert("RGB")
        if status in ("FAILED", "CANCELED"):
            raise RuntimeError(f"DashScope failed: {data}")
        import time

        time.sleep(2)
    raise RuntimeError("DashScope timeout")


def _render_text_overlay(
    base_img,
    metadata: dict[str, Any],
    theme: CoverTheme,
    body: str = "",
):
    from PIL import Image, ImageDraw

    copy = _extract_cover_copy(metadata, body)
    img = base_img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for x in range(0, int(COVER_WIDTH * 0.82)):
        alpha = int(220 * (1 - x / (COVER_WIDTH * 0.82)))
        draw.line([(x, 0), (x, COVER_HEIGHT)], fill=(6, 12, 28, alpha))

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    cn_sub_font = _pick_font(20)
    en_font = _pick_font(16)
    chip_font = _pick_font(15)

    title_font, title_lines, title_line_h = _fit_title_layout(copy.chinese_title, draw)
    y = 48
    for line in title_lines:
        draw.text((44, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_line_h

    if copy.chinese_subtitle:
        sub_lines = _wrap_text_lines(copy.chinese_subtitle, cn_sub_font, draw, 540, max_lines=None)
        y += 6
        for line in sub_lines:
            draw.text((44, y), line, font=cn_sub_font, fill=(210, 220, 240, 255))
            y += 30

    if copy.english_title:
        y += 8
        for line in _wrap_text_lines_words(copy.english_title, en_font, draw, 560, max_lines=2):
            draw.text((44, y), line, font=en_font, fill=(175, 185, 205, 255))
            y += 24

    chip_x = 44
    chip_y = y + 14
    for kw in copy.keywords[:4]:
        label = str(kw)[:14]
        pad_x, pad_y = 11, 5
        tw = _text_width(draw, label, chip_font)
        box = (chip_x, chip_y, chip_x + tw + pad_x * 2, chip_y + 28)
        draw.rounded_rectangle(box, radius=14, fill=(255, 255, 255, 40), outline=theme.accent + (190,), width=1)
        draw.text((chip_x + pad_x, chip_y + pad_y - 1), label, font=chip_font, fill=(0, 0, 0, 255))
        chip_x = box[2] + 10
        if chip_x > 520:
            break

    return img.convert("RGB")


def _generate_local_cover(
    output_path: Path,
    metadata: dict[str, Any],
    body_excerpt: str = "",
    body: str = "",
) -> None:
    theme = enrich_cover_context(metadata, body_excerpt, body=body)
    base = create_theme_background(theme)
    final = _render_text_overlay(base, metadata, theme, body=body)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(output_path, format="PNG", optimize=True)


def _generate_composite_cover(
    output_path: Path,
    metadata: dict[str, Any],
    bg_backend: str,
    body_excerpt: str = "",
    body: str = "",
    overlay_text: bool = True,
) -> None:
    theme = enrich_cover_context(metadata, body_excerpt, body=body)
    prompt = theme.image_prompt or build_image_prompt(metadata, theme, body_excerpt)
    if PURE_IMAGE_SUFFIX not in prompt:
        prompt = f"{prompt} {PURE_IMAGE_SUFFIX}"
    print(f"      prompt preview: {prompt[:220]}...")

    model_used = ""
    try:
        if bg_backend == "siliconflow":
            base, model_used = _fetch_siliconflow_image(prompt)
        elif bg_backend == "pollinations":
            base = _fetch_pollinations_image(prompt)
        else:
            base = _fetch_dashscope_image(prompt)
        base = base.resize((COVER_WIDTH, COVER_HEIGHT))
        label = f"{bg_backend}/{model_used}" if model_used else bg_backend
        print(f"      AI image ready ({label})")
    except Exception as exc:
        print(f"      AI image failed ({exc}), using themed gradient fallback")
        base = create_theme_background(theme)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg_path = output_path.parent / "cover_bg.png"
    base.save(bg_path, format="PNG", optimize=True)

    if overlay_text:
        final = _render_text_overlay(base, metadata, theme, body=body)
        copy = _extract_cover_copy(metadata, body)
        print(f"      overlay: {copy.chinese_title[:24]}... | {copy.english_title[:40]}...")
    else:
        final = base
        print("      pure image mode: no text overlay")

    final.save(output_path, format="PNG", optimize=True)


def _resolve_auto_backend() -> str:
    if SILICONFLOW_API_KEY or os.getenv("SILICONFLOW_API_KEY"):
        return "siliconflow"
    return "pollinations"


def generate_cover_image(
    metadata: dict[str, Any],
    output_path: str | Path,
    backend: str = "auto",
    body_excerpt: str = "",
    body: str = "",
    overlay_text: bool = True,
) -> str:
    """Generate cover image. Returns the backend actually used."""
    path = Path(output_path)
    backend = backend.lower().strip()
    used_backend = backend

    if backend == "auto":
        used_backend = _resolve_auto_backend()
        try:
            _generate_composite_cover(
                path,
                metadata,
                used_backend,
                body_excerpt,
                body=body,
                overlay_text=overlay_text,
            )
            return used_backend
        except Exception as exc:
            print(f"      auto backend fallback to local: {exc}")
            _generate_local_cover(path, metadata, body_excerpt, body=body)
            return "local"
    elif backend == "local":
        _generate_local_cover(path, metadata, body_excerpt, body=body)
    elif backend in ("siliconflow", "sf"):
        _generate_composite_cover(
            path,
            metadata,
            "siliconflow",
            body_excerpt,
            body=body,
            overlay_text=overlay_text,
        )
    elif backend == "pollinations":
        _generate_composite_cover(
            path,
            metadata,
            "pollinations",
            body_excerpt,
            body=body,
            overlay_text=overlay_text,
        )
    elif backend in ("dashscope", "wanx", "tongyi"):
        _generate_composite_cover(
            path,
            metadata,
            "dashscope",
            body_excerpt,
            body=body,
            overlay_text=overlay_text,
        )
    else:
        raise ValueError(f"Unknown backend: {backend}")

    if not path.exists() or path.stat().st_size < 1000:
        raise RuntimeError(f"Cover generation failed: {path}")
    return used_backend


def patch_markdown_with_cover(markdown_text: str, cover_rel: str) -> str:
    if not markdown_text.startswith("---"):
        return markdown_text

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cover_alt = "\u5c01\u9762"
    cover_line = f"![{cover_alt}]({cover_rel})"

    updated = re.sub(
        r"^cover_image:\s*.+$",
        f"cover_image: {json.dumps(cover_rel, ensure_ascii=False)}",
        markdown_text,
        count=1,
        flags=re.MULTILINE,
    )
    if "cover_generated_at:" not in updated:
        updated = re.sub(
            r"^(format:\s*.+)$",
            rf"\1\ncover_generated_at: {generated_at}\ncover_module: cover_generator",
            updated,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        updated = re.sub(
            r"^cover_generated_at:\s*.+$",
            f"cover_generated_at: {generated_at}",
            updated,
            count=1,
            flags=re.MULTILINE,
        )

    front_match = re.match(r"^(---\s*\n.*?\n---\s*\n?)", updated, re.DOTALL)
    if not front_match:
        return updated

    body = updated[front_match.end() :]
    body = PLACEHOLDER_PATTERN.sub("", body)
    body = re.sub(rf"!\[[^\]]*\]\({re.escape(cover_rel)}\)\s*\n?", "", body)

    if cover_line not in body:
        h3_match = re.search(r"^(### .+)$", body, re.MULTILINE)
        if h3_match:
            insert_at = h3_match.end()
            body = body[:insert_at] + f"\n{cover_line}" + body[insert_at:]
        else:
            body = cover_line + "\n" + body

    return front_match.group(1) + body


def regenerate_text_overlay_only(
    cover_abs: Path,
    metadata: dict[str, Any],
    body: str = "",
    body_excerpt: str = "",
) -> None:
    """Re-apply programmatic text on saved cover_bg.png without calling image API."""
    from PIL import Image

    bg_path = cover_abs.parent / "cover_bg.png"
    if not bg_path.exists():
        raise FileNotFoundError(f"Missing background cache: {bg_path}")

    theme = enrich_cover_context(metadata, body_excerpt, body=body)
    base = Image.open(bg_path).convert("RGB")
    final = _render_text_overlay(base, metadata, theme, body=body)
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    final.save(cover_abs, format="PNG", optimize=True)


def generate_cover_for_markdown(
    markdown_path: str | Path,
    summaries_dir: str | Path = DEFAULT_SUMMARIES_DIR,
    backend: str = "auto",
    force: bool = False,
    overlay_text: bool = True,
    text_only: bool = False,
) -> dict[str, Any]:
    md_path = Path(markdown_path).resolve()
    summaries_root = Path(summaries_dir).resolve()

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_front_matter(text)
    arxiv_id = str(meta.get("arxiv_id") or "").strip()
    if not arxiv_id:
        raise ValueError(f"Missing arxiv_id in markdown: {md_path}")

    cover_rel = _cover_rel_path(arxiv_id)
    cover_abs = summaries_root / cover_rel
    existing_cover = str(meta.get("cover_image") or "").strip()
    body_excerpt = re.sub(r"[#>*`\-\[\]()]|\n+", " ", body)
    body_excerpt = re.sub(r"\s+", " ", body_excerpt).strip()[:500]

    index = _load_covers_index()
    if (
        not force
        and not text_only
        and existing_cover
        and (summaries_root / existing_cover).exists()
        and arxiv_id in index
    ):
        return {
            "status": "skipped",
            "markdown_path": str(md_path),
            "arxiv_id": arxiv_id,
            "cover_image": existing_cover,
            "message": "Cover already exists. Use --force to regenerate.",
        }

    if text_only:
        if not overlay_text:
            raise ValueError("--text-only requires overlay mode (do not pass --pure)")
        print(f"  [{arxiv_id}] refreshing text overlay only...")
        regenerate_text_overlay_only(cover_abs, meta, body=body, body_excerpt=body_excerpt)
        used_backend = "text_only"
    else:
        mode = "overlay" if overlay_text else "pure"
        print(f"  [{arxiv_id}] generating cover (backend={backend}, mode={mode})...")
        used_backend = generate_cover_image(
            meta,
            cover_abs,
            backend=backend,
            body_excerpt=body_excerpt,
            body=body,
            overlay_text=overlay_text,
        )

    new_text = patch_markdown_with_cover(text, cover_rel)
    md_path.write_text(new_text, encoding="utf-8")

    try:
        markdown_ref = str(md_path.relative_to(GET_DATA_DIR))
    except ValueError:
        markdown_ref = str(md_path)

    index[arxiv_id] = {
        "markdown": markdown_ref,
        "cover_image": cover_rel,
        "backend": used_backend,
        "image_model": SILICONFLOW_IMAGE_MODEL if used_backend == "siliconflow" else "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_covers_index(index)

    print(f"  [{arxiv_id}] cover saved: {cover_abs}")
    print(f"  [{arxiv_id}] markdown updated: {md_path.name}")

    return {
        "status": "generated",
        "markdown_path": str(md_path),
        "arxiv_id": arxiv_id,
        "cover_image": cover_rel,
        "cover_abs": str(cover_abs),
        "backend": used_backend,
        "image_model": SILICONFLOW_IMAGE_MODEL if used_backend == "siliconflow" else "",
        "pure_image": not overlay_text,
        "text_only": text_only,
    }


def run_pipeline(
    summaries_dir: str | Path = DEFAULT_SUMMARIES_DIR,
    backend: str = "auto",
    max_articles: int = 10,
    force: bool = False,
    overlay_text: bool = True,
) -> list[dict[str, Any]]:
    summaries_root = Path(summaries_dir)
    files = list_markdown_files(summaries_root)
    if not files:
        print("No markdown files found.")
        return []

    print("=" * 60)
    print("  Module 4: Cover Image Generation")
    print(f"  dir: {summaries_root}")
    print(f"  backend: {backend}")
    print(f"  count: {min(len(files), max_articles)}")
    print("=" * 60)

    results: list[dict[str, Any]] = []
    for path in files[:max_articles]:
        try:
            results.append(
                generate_cover_for_markdown(
                    path,
                    summaries_dir=summaries_root,
                    backend=backend,
                    force=force,
                    overlay_text=overlay_text,
                )
            )
        except Exception as exc:
            print(f"  failed {path.name}: {exc}")
            results.append(
                {
                    "status": "error",
                    "markdown_path": str(path),
                    "error": str(exc),
                }
            )
    return results
