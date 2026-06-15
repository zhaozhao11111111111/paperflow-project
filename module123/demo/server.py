from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo"
GET_DATA_DIR = ROOT / "module123" / "get_data"
MODULE4_DIR = GET_DATA_DIR / "module4"
RUNS_DIR = DEMO_DIR / "runs"
SHOWCASE_RUN_IDS = (
    "20260614-162715-8b8a0d",
    "20260614-161706-5e537b",
    "20260614-161344-9eb0e8",
)

for path in (GET_DATA_DIR, MODULE4_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(MODULE4_DIR / "env.bundle")
_load_env(MODULE4_DIR / ".env")
os.environ.setdefault("PAPERFLOW_HEADLESS", "1")

from cover_generator_core import generate_cover_for_markdown  # noqa: E402
from md_to_wechat_core import parse_front_matter, render_preview_html  # noqa: E402
from paper_crawler_core import run_pipeline as crawl_papers  # noqa: E402
from paper_summarizer_core import summarize_paper  # noqa: E402


STATE_LOCK = threading.Lock()
RUN_STATE: dict[str, object] = {
    "status": "idle",
    "run_id": "",
    "step": -1,
    "steps": ["idle"] * 5,
    "message": "等待运行",
    "result": None,
    "error": "",
}


def _set_state(**updates: object) -> None:
    with STATE_LOCK:
        RUN_STATE.update(updates)


def _set_step(index: int, message: str) -> None:
    with STATE_LOCK:
        steps = list(RUN_STATE["steps"])
        for i in range(index):
            steps[i] = "done"
        steps[index] = "running"
        RUN_STATE.update(step=index, steps=steps, message=message)


def _complete_step(index: int) -> None:
    with STATE_LOCK:
        steps = list(RUN_STATE["steps"])
        steps[index] = "done"
        RUN_STATE["steps"] = steps


def _relative_url(path: Path) -> str:
    return "/" + path.resolve().relative_to(ROOT).as_posix()


def _versioned_url(path: Path) -> str:
    return f"{_relative_url(path)}?v={path.stat().st_mtime_ns}"


def _http_preview_images(preview_path: Path) -> None:
    text = preview_path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        parsed = urlparse(match.group(0))
        local = Path(unquote(parsed.path))
        try:
            return _relative_url(local)
        except ValueError:
            return match.group(0)

    text = re.sub(r"file://[^\"']+", replace, text)
    preview_path.write_text(text, encoding="utf-8")


def _restore_latest_result() -> None:
    if not RUNS_DIR.exists():
        return
    run_dirs = sorted((path for path in RUNS_DIR.iterdir() if path.is_dir()), reverse=True)
    for run_root in run_dirs:
        markdown_files = sorted(
            path for path in (run_root / "summaries").glob("*.md")
            if path.name != "index.md"
        )
        preview_files = sorted((run_root / "wechat").glob("*.preview.html"))
        cover_files = sorted((run_root / "summaries" / "images").glob("*/cover.png"))
        paper_files = sorted((run_root / "papers").glob("*.json"))
        paper_files = [path for path in paper_files if path.name != "checked.json"]
        if not (markdown_files and preview_files and cover_files and paper_files):
            continue
        markdown_path = markdown_files[-1]
        metadata, _body = parse_front_matter(markdown_path.read_text(encoding="utf-8"))
        result = {
            "title": str(metadata.get("wechat_title") or metadata.get("paper_title") or "论文文章"),
            "cover_url": _versioned_url(cover_files[-1]),
            "preview_url": _relative_url(preview_files[-1]),
            "paper_json_url": _relative_url(paper_files[-1]),
            "markdown_url": _relative_url(markdown_path),
        }
        _set_state(
            status="complete",
            run_id=run_root.name,
            step=4,
            steps=["done"] * 5,
            message="流水线运行完成",
            result=result,
            error="",
        )
        return


def _collect_history() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not RUNS_DIR.exists():
        return records
    for run_id in SHOWCASE_RUN_IDS:
        run_root = RUNS_DIR / run_id
        if not run_root.is_dir():
            continue
        markdown_files = sorted(
            path for path in (run_root / "summaries").glob("*.md")
            if path.name != "index.md"
        )
        preview_files = sorted((run_root / "wechat").glob("*.preview.html"))
        cover_files = sorted((run_root / "summaries" / "images").glob("*/cover.png"))
        paper_files = [
            path for path in sorted((run_root / "papers").glob("*.json"))
            if path.name != "checked.json"
        ]
        if not (markdown_files and preview_files and cover_files and paper_files):
            continue
        markdown_path = markdown_files[-1]
        paper_path = paper_files[-1]
        metadata, _body = parse_front_matter(markdown_path.read_text(encoding="utf-8"))
        try:
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
        except Exception:
            paper = {}
        abstract = re.sub(r"\s+", " ", str(paper.get("abstract") or paper.get("summary") or "")).strip()
        inline_images = metadata.get("inline_images")
        if not isinstance(inline_images, list):
            inline_images = []
        records.append({
            "run_id": run_root.name,
            "title": str(metadata.get("wechat_title") or metadata.get("paper_title") or "论文文章"),
            "paper_title": str(metadata.get("paper_title") or paper.get("title") or ""),
            "arxiv_id": str(metadata.get("arxiv_id") or paper.get("arxiv_id") or ""),
            "query": str(metadata.get("search_query") or paper.get("search_query") or ""),
            "generated_at": str(metadata.get("generated_at") or paper.get("crawled_at") or ""),
            "abstract": abstract[:180],
            "inline_image_count": len(inline_images),
            "cover_url": _versioned_url(cover_files[-1]),
            "preview_url": _relative_url(preview_files[-1]),
            "paper_json_url": _relative_url(paper_path),
            "markdown_url": _relative_url(markdown_path),
        })
    return records


def _run_real_pipeline(query: str, run_id: str) -> None:
    run_root = RUNS_DIR / run_id
    papers_dir = run_root / "papers"
    summaries_dir = run_root / "summaries"
    previews_dir = run_root / "wechat"
    for path in (papers_dir, summaries_dir, previews_dir):
        path.mkdir(parents=True, exist_ok=True)

    try:
        _set_step(0, "正在检索 arXiv 最新论文")
        paper_files = crawl_papers(
            query=query,
            select_top_n=1,
            output_dir=str(papers_dir),
            max_browse=30,
            max_llm=5,
        )
        if not paper_files:
            raise RuntimeError("没有找到符合条件的新论文，请更换关键词后重试。")
        paper_path = Path(paper_files[0])
        _complete_step(0)
        _set_step(1, "已完成大模型相关性筛选")
        _complete_step(1)

        _set_step(2, "正在解析 PDF 并生成 Markdown")
        markdown_result = summarize_paper(
            str(paper_path),
            output_dir=str(summaries_dir),
            use_pdf=True,
            force=True,
        )
        if not markdown_result:
            raise RuntimeError("论文总结生成失败。")
        markdown_path = Path(markdown_result)
        _complete_step(2)

        _set_step(3, "正在调用生图 API 生成主题封面")
        cover_result = generate_cover_for_markdown(
            markdown_path,
            summaries_dir=summaries_dir,
            backend="auto",
            force=True,
        )
        cover_path = Path(str(cover_result["cover_abs"]))
        _complete_step(3)

        _set_step(4, "正在生成微信公众号 HTML 预览")
        preview_path = render_preview_html(markdown_path, previews_dir)
        _http_preview_images(preview_path)
        markdown_text = markdown_path.read_text(encoding="utf-8")
        metadata, _body = parse_front_matter(markdown_text)
        title = str(metadata.get("wechat_title") or metadata.get("paper_title") or "论文文章")
        _complete_step(4)

        result = {
            "title": title,
            "cover_url": _versioned_url(cover_path),
            "preview_url": _relative_url(preview_path),
            "paper_json_url": _relative_url(paper_path),
            "markdown_url": _relative_url(markdown_path),
        }
        _set_state(status="complete", message="流水线运行完成", result=result)
    except Exception as exc:
        traceback.print_exc()
        _set_state(status="error", message="运行失败", error=str(exc))


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            with STATE_LOCK:
                self._send_json(dict(RUN_STATE))
            return
        if path == "/api/history":
            self._send_json({"runs": _collect_history()})
            return
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/demo/")
            self.end_headers()
            return
        allowed = (
            path == "/demo"
            or path.startswith("/demo/")
            or path.startswith("/module123/get_data/summaries/images/")
        )
        if not allowed:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "请求格式错误"}, HTTPStatus.BAD_REQUEST)
            return
        query = str(payload.get("query", "")).strip()
        if not query:
            self._send_json({"error": "请输入研究主题"}, HTTPStatus.BAD_REQUEST)
            return
        with STATE_LOCK:
            if RUN_STATE["status"] == "running":
                self._send_json({"error": "已有任务正在运行"}, HTTPStatus.CONFLICT)
                return
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            RUN_STATE.update(
                status="running",
                run_id=run_id,
                step=0,
                steps=["idle"] * 5,
                message="正在启动流水线",
                result=None,
                error="",
            )
        thread = threading.Thread(
            target=_run_real_pipeline,
            args=(query, run_id),
            daemon=True,
        )
        thread.start()
        self._send_json({"run_id": run_id, "status": "running"}, HTTPStatus.ACCEPTED)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[server] {self.address_string()} - {fmt % args}")


def main() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _restore_latest_result()
    host = "127.0.0.1"
    port = int(os.getenv("PAPERFLOW_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"PaperFlow demo: http://{host}:{port}/demo/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
