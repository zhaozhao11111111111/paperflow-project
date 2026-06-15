# 知流研究台演示站

页面可以调用项目中的真实爬虫、LLM、封面生成和公众号预览代码。

首次运行先安装依赖：

```bash
python3 -m venv demo/.venv
demo/.venv/bin/pip install -r demo/requirements.txt
```

启动本地服务：

```bash
demo/.venv/bin/python demo/server.py
```

然后访问：

```text
http://localhost:8000/demo/
```

点击“开始运行”后会调用真实 API，并把本次产物保存到 `demo/runs/`。公众号步骤生成本地 HTML 预览，不会提交正式发布。
