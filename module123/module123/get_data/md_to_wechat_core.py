"""
模块3核心实现：Markdown -> 微信公众号草稿/发布。

本模块不依赖真实 MCP 运行环境，提供可被命令行、MCP Server 或流水线脚本复用的函数。
真实上传/发布使用微信公众号「订阅号/服务号」后台的开发者接口。
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SUMMARIES_DIR = BASE_DIR / "summaries"
DEFAULT_OUTPUT_DIR = BASE_DIR / "wechat_articles"
PUBLISHED_INDEX_FILE = "published.json"


class WeChatPublishError(RuntimeError):
    """微信公众号接口调用失败。"""


@dataclass
class ArticlePackage:
    markdown_path: Path
    title: str
    digest: str
    content_html: str
    content_source_url: str
    cover_image: str
    thumb_media_id: str
    metadata: dict[str, Any]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """解析模块2生成的简单 YAML front matter。"""
    if not text.startswith("---"):
        return {}, text

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text

    meta: dict[str, Any] = {}
    for raw_line in match.group(1).splitlines():
        if ":" not in raw_line:
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value == "":
            meta[key] = ""
            continue
        if raw_value.startswith("[") or raw_value.startswith("{"):
            try:
                meta[key] = json.loads(raw_value)
                continue
            except Exception:
                pass
        meta[key] = _strip_quotes(raw_value)

    return meta, text[match.end() :]


def load_markdown_article(markdown_path: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(markdown_path)
    text = path.read_text(encoding="utf-8")
    return parse_front_matter(text)


def list_markdown_files(summaries_dir: str | Path = DEFAULT_SUMMARIES_DIR) -> list[Path]:
    root = Path(summaries_dir)
    if not root.exists():
        return []
    return sorted(
        p for p in root.glob("*.md") if p.name.lower() != "index.md" and p.is_file()
    )


def _inline_markdown_to_html(text: str) -> str:
    placeholders: list[str] = []

    def hold(value: str) -> str:
        placeholders.append(value)
        return f"\u0000{len(placeholders) - 1}\u0000"

    text = html.escape(text, quote=False)
    text = re.sub(
        r"`([^`]+)`",
        lambda m: hold(f"<code>{html.escape(m.group(1))}</code>"),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: hold(
            f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>'
        ),
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)

    for i, value in enumerate(placeholders):
        text = text.replace(f"\u0000{i}\u0000", value)
    return text


def _resolve_image_path(src: str, markdown_path: Path) -> Path | None:
    if re.match(r"^https?://", src):
        return None
    path = (markdown_path.parent / src).resolve()
    return path if path.exists() else None


def _image_tag(alt: str, src: str) -> str:
    safe_alt = html.escape(alt, quote=True)
    safe_src = html.escape(src, quote=True)
    return (
        '<p style="margin: 18px 0; text-align: center;">'
        f'<img src="{safe_src}" alt="{safe_alt}" '
        'style="max-width: 100%; height: auto; border-radius: 4px;"/></p>'
    )


def markdown_to_wechat_html(
    markdown_body: str,
    markdown_path: str | Path,
    image_url_map: dict[str, str] | None = None,
) -> str:
    """将模块2 Markdown 正文转换为适合微信公众号图文消息的内联样式 HTML。"""
    md_path = Path(markdown_path)
    image_url_map = image_url_map or {}
    lines = markdown_body.splitlines()
    html_lines: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html_lines.append("</ul>")
            list_open = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            close_list()
            continue

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        image_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if image_match:
            close_list()
            alt, src = image_match.groups()
            final_src = image_url_map.get(src, src)
            html_lines.append(_image_tag(alt, final_src))
            continue

        if stripped == "---":
            close_list()
            html_lines.append(
                '<hr style="border: 0; border-top: 1px solid #e8e8e8; margin: 24px 0;"/>'
            )
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = len(heading.group(1))
            content = _inline_markdown_to_html(heading.group(2))
            if level == 1:
                html_lines.append(
                    '<h1 style="font-size: 22px; line-height: 1.45; margin: 0 0 16px; '
                    f'font-weight: 700; color: #111;">{content}</h1>'
                )
            elif level == 2:
                html_lines.append(
                    '<h2 style="font-size: 18px; line-height: 1.55; margin: 28px 0 12px; '
                    'padding-left: 10px; border-left: 4px solid #2f7df6; '
                    f'font-weight: 700; color: #111;">{content}</h2>'
                )
            else:
                html_lines.append(
                    '<h3 style="font-size: 15px; line-height: 1.6; margin: 18px 0 8px; '
                    f'font-weight: 700; color: #333;">{content}</h3>'
                )
            continue

        if stripped.startswith(">"):
            close_list()
            content = _inline_markdown_to_html(stripped.lstrip("> ").strip())
            html_lines.append(
                '<blockquote style="margin: 16px 0; padding: 12px 14px; '
                'background: #f6f8fb; border-left: 4px solid #2f7df6; '
                f'color: #444; line-height: 1.8;">{content}</blockquote>'
            )
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            if not list_open:
                html_lines.append(
                    '<ul style="padding-left: 1.2em; margin: 10px 0 16px; color: #222;">'
                )
                list_open = True
            content = _inline_markdown_to_html(bullet.group(1))
            html_lines.append(f'<li style="margin: 6px 0; line-height: 1.8;">{content}</li>')
            continue

        close_list()
        content = _inline_markdown_to_html(stripped)
        html_lines.append(
            '<p style="font-size: 15px; line-height: 1.9; margin: 12px 0; '
            f'color: #222;">{content}</p>'
        )

    close_list()
    return "\n".join(html_lines)


def build_digest(markdown_body: str, metadata: dict[str, Any], max_len: int = 120) -> str:
    if metadata.get("digest"):
        return str(metadata["digest"])[:max_len]

    body = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown_body)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = re.sub(r"[#>*_`\-\[\]()]|\n+", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:max_len]


def _load_published_index(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    path = Path(output_dir) / PUBLISHED_INDEX_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_published_index(index: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / PUBLISHED_INDEX_FILE
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


class WeChatOfficialAccountClient:
    """微信公众号官方接口客户端。"""

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.app_id = app_id or os.getenv("WECHAT_APP_ID", "")
        self.app_secret = app_secret or os.getenv("WECHAT_APP_SECRET", "")
        self._access_token = access_token or os.getenv("WECHAT_ACCESS_TOKEN", "")

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        resp = requests.request(method, url, timeout=60, **kwargs)
        try:
            data = resp.json()
        except Exception as exc:
            raise WeChatPublishError(f"微信接口返回非JSON: {resp.text[:200]}") from exc
        if data.get("errcode") not in (None, 0):
            raise WeChatPublishError(f"微信接口错误 {data.get('errcode')}: {data.get('errmsg')}")
        return data

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.app_id or not self.app_secret:
            raise WeChatPublishError(
                "缺少 WECHAT_APP_ID / WECHAT_APP_SECRET，无法获取 access_token。"
            )
        url = (
            "https://api.weixin.qq.com/cgi-bin/token"
            f"?grant_type=client_credential&appid={self.app_id}&secret={self.app_secret}"
        )
        data = self._request_json("GET", url)
        self._access_token = data["access_token"]
        return self._access_token

    def upload_content_image(self, image_path: str | Path) -> str:
        """上传正文图片，返回可放入 content 的微信图片 URL。"""
        token = self.access_token()
        path = Path(image_path)
        url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as f:
            data = self._request_json("POST", url, files={"media": (path.name, f, mime)})
        return data["url"]

    def upload_cover_material(self, image_path: str | Path) -> str:
        """上传封面图为永久素材，返回 thumb_media_id。"""
        token = self.access_token()
        path = Path(image_path)
        url = (
            "https://api.weixin.qq.com/cgi-bin/material/add_material"
            f"?access_token={token}&type=thumb"
        )
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as f:
            data = self._request_json("POST", url, files={"media": (path.name, f, mime)})
        return data["media_id"]

    def add_draft(self, article: ArticlePackage) -> str:
        token = self.access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
        author = os.getenv("WECHAT_AUTHOR", "自动化文献总结流水线")
        payload = {
            "articles": [
                {
                    "title": article.title[:64],
                    "author": author[:8],
                    "digest": article.digest[:120],
                    "content": article.content_html,
                    "content_source_url": article.content_source_url,
                    "thumb_media_id": article.thumb_media_id,
                    "need_open_comment": 0,
                    "only_fans_can_comment": 0,
                }
            ]
        }
        data = self._request_json("POST", url, json=payload)
        return data["media_id"]

    def publish_draft(self, media_id: str) -> dict[str, Any]:
        token = self.access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={token}"
        return self._request_json("POST", url, json={"media_id": media_id})


def prepare_article_package(
    markdown_path: str | Path,
    client: WeChatOfficialAccountClient | None = None,
    upload_images: bool = False,
    thumb_media_id: str | None = None,
) -> ArticlePackage:
    md_path = Path(markdown_path).resolve()
    metadata, body = load_markdown_article(md_path)
    title = str(metadata.get("wechat_title") or metadata.get("title") or "").strip()
    if not title:
        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else md_path.stem

    client = client or WeChatOfficialAccountClient()
    image_url_map: dict[str, str] = {}
    cover = str(metadata.get("cover_image") or "").strip()
    resolved_cover = _resolve_image_path(cover, md_path) if cover else None

    if upload_images:
        for alt, src in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", body):
            local_path = _resolve_image_path(src, md_path)
            if local_path:
                image_url_map[src] = client.upload_content_image(local_path)

    final_thumb_media_id = thumb_media_id or os.getenv("WECHAT_DEFAULT_THUMB_MEDIA_ID", "")
    if upload_images and not final_thumb_media_id and resolved_cover:
        final_thumb_media_id = client.upload_cover_material(resolved_cover)

    content_html = markdown_to_wechat_html(body, md_path, image_url_map=image_url_map)
    source_url = str(metadata.get("entry_id") or metadata.get("pdf_url") or "")
    digest = build_digest(body, metadata)

    return ArticlePackage(
        markdown_path=md_path,
        title=title,
        digest=digest,
        content_html=content_html,
        content_source_url=source_url,
        cover_image=cover,
        thumb_media_id=final_thumb_media_id,
        metadata=metadata,
    )


def render_preview_html(
    markdown_path: str | Path,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    package = prepare_article_package(markdown_path, upload_images=False)
    _, body = load_markdown_article(package.markdown_path)
    local_image_map: dict[str, str] = {}
    for _alt, src in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", body):
        local_path = _resolve_image_path(src, package.markdown_path)
        if local_path:
            local_image_map[src] = local_path.as_uri()
    content_html = markdown_to_wechat_html(
        body,
        package.markdown_path,
        image_url_map=local_image_map,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / f"{package.markdown_path.stem}.preview.html"
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(package.title)}</title>
</head>
<body style="margin:0; background:#f5f5f5;">
  <main style="max-width: 680px; margin: 0 auto; padding: 24px 18px; background: #fff;">
{content_html}
  </main>
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")
    return out_path


def upload_markdown_to_draft(
    markdown_path: str | Path,
    thumb_media_id: str | None = None,
    dry_run: bool = True,
    publish: bool = False,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """转换 Markdown，并按需上传为草稿/发布。dry_run=True 时只生成预览。"""
    preview_path = render_preview_html(markdown_path, output_dir)
    package = prepare_article_package(
        markdown_path,
        upload_images=not dry_run,
        thumb_media_id=thumb_media_id,
    )

    result: dict[str, Any] = {
        "markdown_path": str(package.markdown_path),
        "title": package.title,
        "digest": package.digest,
        "preview_html": str(preview_path),
        "dry_run": dry_run,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if dry_run:
        result["status"] = "preview_generated"
        return result

    if not package.thumb_media_id:
        raise WeChatPublishError(
            "缺少封面 thumb_media_id。请提供 --thumb-media-id，或设置 "
            "WECHAT_DEFAULT_THUMB_MEDIA_ID，或在 Markdown front matter 中配置 cover_image。"
        )

    client = WeChatOfficialAccountClient()
    media_id = client.add_draft(package)
    result["draft_media_id"] = media_id
    result["status"] = "draft_created"

    if publish:
        publish_result = client.publish_draft(media_id)
        result["publish_result"] = publish_result
        result["status"] = "publish_submitted"

    index = _load_published_index(output_dir)
    key = str(package.metadata.get("arxiv_id") or package.markdown_path.stem)
    index[key] = result
    _save_published_index(index, output_dir)
    return result


def batch_upload_latest(
    summaries_dir: str | Path = DEFAULT_SUMMARIES_DIR,
    max_articles: int = 1,
    dry_run: bool = True,
    publish: bool = False,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> list[dict[str, Any]]:
    files = list_markdown_files(summaries_dir)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for path in files[:max_articles]:
        results.append(upload_markdown_to_draft(path, dry_run=dry_run, publish=publish, output_dir=output_dir))
        if not dry_run:
            time.sleep(1)
    return results
