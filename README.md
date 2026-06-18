# 自动化文献总结与公众号内容生成流水线

本仓库包含论文检索、结构化总结、公众号预览、封面生成和演示站相关代码。目录已按“源码、演示、归档”重新整理，避免运行缓存、报告材料和源码混在一起。

## 目录结构

```text
.
├── module123/
│   ├── demo/                         # 本地演示站
│   └── get_data/                     # 流水线核心代码与样例数据
└── archive/
    ├── demo_runs/                    # 已归档的历史演示运行结果
    └── legacy/                       # 旧版或备份代码
```

## 运行核心流水线

```bash
cd module123/get_data
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python paper_crawler.py
.venv/bin/python paper_summarizer.py
```

公众号预览：

```bash
.venv/bin/python md_to_wechat.py latest --max-articles 1
```

封面生成依赖位于 `module4/requirements.txt`，需要单独安装对应依赖并配置 API 环境变量。

## 运行演示站

```bash
cd module123
python3 -m venv demo/.venv
demo/.venv/bin/pip install -r demo/requirements.txt
demo/.venv/bin/python demo/server.py
```

浏览器访问：

```text
http://localhost:8000/demo/
```
```

## 归档说明

`archive/demo_runs/` 保存旧演示运行结果；`archive/legacy/` 保存不参与当前主流程的旧版代码。若确认不再需要，可以手动删除这些归档目录。
