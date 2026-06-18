# arXiv 论文爬取与智能筛选

基于 Selenium 模拟浏览器行为，自动搜索 arXiv、筛选近期论文，并调用大模型判断相关性后保存到本地。

## 功能

- **浏览器模拟搜索** — 用 Selenium 驱动 Chrome 打开 arXiv，输入关键词搜索，完全模拟真人操作
- **日期自动过滤** — 根据 arXiv ID 前4位（YYMM）判断发表月份，只关注最近一个月的论文
- **摘要自动补全** — 搜索结果页摘要过短时，自动打开详情页获取完整摘要
- **大模型智能筛选** — 调用 DeepSeek 大模型逐篇判断论文与搜索关键词的相关性（最多调用5次）
- **额外筛选要求** — 可附加自定义条件（如"必须是交叉学科"），注入大模型提示词中
- **去重保存** — 先检查本地是否已保存，再调用大模型，避免重复保存和浪费调用
- **安全上限** — 浏览arXiv结果100条或大模型调用5次后自动终止，达到任一即停
- **索引维护** — 每次保存后自动更新 `papers/quick_result.md`，汇总所有已保存论文的信息

## 文件结构

```
get_data/
├── paper_crawler.py             # 模块1入口（命令行调用）
├── paper_crawler_core.py        # 模块1核心（Selenium + 大模型筛选）
├── paper_summarizer.py          # 模块2入口（命令行调用）
├── paper_summarizer_core.py     # 模块2核心（LLM 结构化总结）
├── papers/                      # 模块1输出：论文 JSON
│   ├── *.json
│   └── quick_result.md
└── summaries/                   # 模块2输出：Markdown 总结
    ├── *.md
    ├── index.md
    └── summarized.json
```

## 依赖

```bash
pip install selenium webdriver-manager openai
```

需要本地安装 Chrome 浏览器（webdriver-manager 会自动下载对应版本的 ChromeDriver）。

## 调用方式

直接运行，按提示输入参数（直接回车使用默认值）：

```bash
python paper_crawler.py
```

运行示例：

```
==================================================
  arXiv 论文爬取与智能筛选
  直接回车使用默认值
==================================================

  搜索关键词 (默认: retrieval augmented generation): LLM reasoning
  保存论文篇数 (默认: 2): 3
  额外筛选要求 (可选，直接回车跳过): 必须涉及数学推理或形式化验证
```

### Python 调用

```python
from paper_crawler_core import run_pipeline

run_pipeline(
    query="LLM reasoning",
    select_top_n=3,
    extra_query="必须涉及数学推理或形式化验证",
)
```

## 运行流程

1. 打开 Chrome 浏览器，访问 arXiv 搜索页
2. 输入关键词并搜索
3. 逐条浏览搜索结果（最多100条）：
   - 跳过 arXiv ID 不在最近一个月范围内的论文
   - **先检查本地是否已保存**，已保存的直接跳过
   - 摘要过短时自动进入详情页补充
   - **然后调用大模型**判断相关性（最多调用5次）
4. 匹配则保存到 `papers/` 目录（JSON 格式），不匹配则继续
5. 保存后自动更新 `papers/quick_result.md` 索引
6. 达到以下任一条件即停止：目标保存数量 / 浏览100条 / 大模型调用5次

## 配置

在 [paper_crawler_core.py](paper_crawler_core.py) 顶部修改大模型配置：

```python
LLM_API_KEY = "your-api-key"
LLM_BASE_URL = "https://your-api-base-url"
LLM_MODEL = "deepseek-chat"  # 可选: deepseek-chat, qwen, glm 等
```

---

# 模块2：论文 → 公众号推文 Markdown

读取模块1保存在 `papers/` 下的 JSON，调用大模型生成**公众号风格**中文推文（参考 [PaperWeekly](https://www.jiqizhixin.com/columns/paperweekly) / 机器之心论文解读栏目）。

## 功能

- **吸睛标题** — 生成 `wechat_title`，不是照搬英文论文标题
- **封面图 + 正文配图** — 按 PDF 中 `Figure/Table` 标题定位并裁剪渲染（非内嵌图提取）；封面留空待模块4 AI 生成
- **公众号排版** — 导语 blockquote、短段落、划重点、原文链接
- **摘要补全** — 本地摘要被截断时，自动调用 arXiv API 获取完整摘要
- **YAML 元数据** — 含 `wechat_title`、`cover_image`、`inline_images`，方便模块3/4对接
- **索引维护** — 自动更新 `summaries/index.md` 与 `summarized.json`

## 依赖

```bash
pip install openai requests pymupdf
```

> 配图说明：学术论文 PDF 中的图表多为矢量绘制，不能靠 `get_images()` 抠内嵌图。模块2 会识别 `Figure 1` / `Table 2` 等标题，裁剪对应区域并渲染为 PNG。

大模型配置在 [paper_summarizer_core.py](paper_summarizer_core.py) 顶部（与模块1相同）。

## 调用方式

```bash
python paper_summarizer.py
```

运行示例：

```
  单次最多处理篇数 (默认: 5): 2
  是否下载 PDF 提取封面/配图 (y/n) (默认: True):
  是否强制重新总结已处理论文 (y/n，默认 n): y
```

### Python 调用

```python
from paper_summarizer_core import run_pipeline, summarize_paper

# 批量处理（默认开启 PDF 配图）
run_pipeline(max_papers=3)

# 强制重新生成单篇
summarize_paper("papers/2605_22203_20260524_142125.json", force=True)
```

## 输出格式（公众号推文）

```markdown
---
wechat_title: "低资源语言RAG怎么切块？字符递归法碾压LLM方案"
cover_image: "images/2605_22203/cover.png"
format: wechat_article
---

# 低资源语言RAG怎么切块？字符递归法碾压LLM方案
### 副标题
![封面](images/2605_22203/cover.png)
> 导语...

**论文**：...  **作者**：...  **来源**：[arXiv:...](...)

## 为什么要关注这篇论文？
...

## 核心方法
...
![图1：...](images/2605_22203/fig_p4_1.png)

📄 **阅读原文**：[PDF 下载](...) | [arXiv 页面](...)
```

## 与模块3/4对接

| 字段 | 用途 |
|------|------|
| `wechat_title` | 公众号文章标题 |
| `cover_image` | 正文顶部封面（模块4 可替换为 AI 生成封面） |
| `inline_images` | 正文插图列表 |
| `format: wechat_article` | 标识公众号格式，模块3 按此排版 |

---

# 模块3：Markdown → 微信公众号（基于 MCP）

模块3负责将模块2生成的公众号推文 Markdown 转换为微信公众号图文消息，并通过 MCP Server 将“预览、创建草稿、提交发布”等能力封装为可调用工具。该模块既可以独立命令行运行，也可以被模块5流水线统一编排。

## 功能说明

模块3读取模块2输出的 `summaries/*.md`，完成以下处理：

- 解析 Markdown front matter，提取 `wechat_title`、`cover_image`、`inline_images`、`entry_id`、`pdf_url` 等字段
- 将 Markdown 标题、段落、引用、列表、加粗、链接、图片等元素转换为微信公众号可用的 HTML
- 为 HTML 添加内联样式，适配微信公众号图文编辑器的显示方式
- 在真实上传时，将正文图片上传到微信图片接口，并替换为微信返回的图片 URL
- 根据封面图或默认封面素材生成 `thumb_media_id`，满足公众号草稿接口要求
- 调用微信公众号草稿箱接口创建图文草稿
- 可选调用发布接口，将草稿提交发布
- 通过 MCP Server 暴露标准工具，供流水线或 MCP Client 调用

## 新增文件

```text
get_data/
├── md_to_wechat.py          # 模块3命令行入口
├── md_to_wechat_core.py     # 模块3核心逻辑：解析、排版、上传、发布
├── wechat_mcp_server.py     # MCP Server，暴露公众号发布工具
└── wechat_articles/         # 预览HTML与发布记录输出目录
```

## 输入与输出

输入：

- `summaries/*.md`：模块2生成的公众号推文 Markdown
- `summaries/images/...`：模块2抽取的论文配图
- 可选的 `cover_image`：模块4生成或人工准备的封面图

输出：

- `wechat_articles/*.preview.html`：本地排版预览文件
- `wechat_articles/published.json`：真实上传/发布后的记录
- 微信公众号草稿箱中的图文草稿
- 可选的微信公众号发布任务

## 依赖

```bash
pip install requests
pip install mcp   # 只有运行 MCP Server 时需要
```

## 本地预览（不需要微信权限）

本地预览只读取 Markdown 和本地图片，生成 HTML 文件，不调用微信接口：

```bash
python md_to_wechat.py preview summaries/2605_22203_20260525_201122.md
```

也可以处理最新一篇 Markdown：

```bash
python md_to_wechat.py latest --max-articles 1
```

输出文件会写入 `wechat_articles/*.preview.html`。

## 微信公众号授权配置

真实上传草稿或提交发布前，需要在微信公众号后台完成以下配置：

1. 登录微信公众号平台：`https://mp.weixin.qq.com`
2. 进入“设置与开发”中的开发者配置页面
3. 获取公众号的 `AppID` 和 `AppSecret`
4. 将运行本项目的机器公网 IP 加入 IP 白名单
5. 确认账号具备素材管理、草稿箱、发布相关接口权限
6. 准备封面图素材，或由模块4生成封面后交给模块3上传

在本地运行环境中配置环境变量：

PowerShell：

```powershell
$env:WECHAT_APP_ID="你的AppID"
$env:WECHAT_APP_SECRET="你的AppSecret"
$env:WECHAT_DEFAULT_THUMB_MEDIA_ID="默认封面素材media_id"
$env:WECHAT_AUTHOR="自动化文献总结流水线"
```

Windows CMD：

```bash
set WECHAT_APP_ID=你的AppID
set WECHAT_APP_SECRET=你的AppSecret
set WECHAT_DEFAULT_THUMB_MEDIA_ID=默认封面素材media_id
set WECHAT_AUTHOR=自动化文献总结流水线
```

说明：

- `WECHAT_APP_ID`：微信公众号开发者 ID
- `WECHAT_APP_SECRET`：微信公众号开发者密钥，用于获取 `access_token`
- `WECHAT_DEFAULT_THUMB_MEDIA_ID`：默认封面素材 ID。若 Markdown 中存在可用 `cover_image`，模块3也可以上传该图片生成封面素材 ID
- `WECHAT_AUTHOR`：图文消息作者字段

## 真实上传草稿

创建草稿：

```bash
python md_to_wechat.py draft summaries/2605_22203_20260525_201122.md --real
```

创建草稿并提交发布：

```bash
python md_to_wechat.py draft summaries/2605_22203_20260525_201122.md --real --publish
```

如果已有封面素材 ID，也可以显式传入：

```bash
python md_to_wechat.py draft summaries/2605_22203_20260525_201122.md --real --thumb-media-id MEDIA_ID
```

执行真实上传时，模块3会依次完成：

1. 获取微信公众号 `access_token`
2. 上传 Markdown 正文中的本地图片，替换为微信图片 URL
3. 获取或上传封面图素材，得到 `thumb_media_id`
4. 调用 `draft/add` 接口创建草稿
5. 如果指定 `--publish`，继续调用 `freepublish/submit` 接口提交发布

## MCP 调用方式

启动 MCP Server：

```bash
python wechat_mcp_server.py
```

暴露的 MCP 工具：

| 工具 | 作用 |
|------|------|
| `list_wechat_markdown` | 列出可发布的 Markdown |
| `preview_wechat_article` | 生成本地预览 HTML |
| `create_wechat_draft` | 创建公众号草稿 |
| `publish_wechat_article` | 创建草稿并提交发布 |
| `publish_latest_wechat_articles` | 批量处理最新 N 篇，供模块5编排 |

MCP 工具调用示例：

```json
{
  "tool": "create_wechat_draft",
  "arguments": {
    "markdown_path": "summaries/2605_22203_20260525_201122.md",
    "dry_run": false,
    "thumb_media_id": "MEDIA_ID"
  }
}
```

```json
{
  "tool": "publish_latest_wechat_articles",
  "arguments": {
    "max_articles": 1,
    "dry_run": false,
    "publish": true
  }
}
```

## 需要哪些权限

本地预览只需要读取 `summaries/` 和 `summaries/images/`，并写入 `wechat_articles/`，不需要微信账号权限。

真实上传/发布需要：

- 已注册并可登录的微信公众号，且后台开启开发者接口
- `AppID` 和 `AppSecret`
- 将运行机器公网 IP 加入微信公众号后台的 IP 白名单，否则无法获取 `access_token`
- 素材管理与草稿箱接口权限，用于上传正文图片、上传封面、创建草稿
- 发布接口权限，用于 `freepublish/submit` 提交发布
- 封面图素材：公众号草稿接口要求 `thumb_media_id`，可由模块4生成封面后上传，也可提前在素材库准备默认封面
