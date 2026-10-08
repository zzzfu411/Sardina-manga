<p align="center">
  <img src="web/brand/sardina-097-c.png" width="260" alt="Sardina：一条正在看漫画的小鱼">
</p>

<h1 align="center">Sardina manga</h1>

<p align="center">在浏览器里搜索、收藏和阅读漫画。</p>

<p align="center">
  <a href="https://github.com/zzzfu411/Sardina-manga/actions/workflows/checks.yml"><img src="https://github.com/zzzfu411/Sardina-manga/actions/workflows/checks.yml/badge.svg" alt="自动检查"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+"></a>
</p>

<p align="center">
  <a href="https://manga.yeuxark.com">在线阅读</a> · <a href="#功能">功能</a> · <a href="#开始使用">本机运行</a> · <a href="docs/DEPLOYMENT.md">部署</a> · <a href="docs/API.md">API 文档</a>
</p>

Sardina 可在本机运行，也可部署为网站，由后端连接漫画源。前端使用原生 JavaScript 和 CSS，后端使用 Python，无需前端构建。

## 功能

- **多源搜索**：按作品整理搜索结果，可查看和切换来源。
- **发现漫画**：排行榜、最近更新，以及结合本地书架偏好的封面推荐。
- **阅读器**：连续滚动或逐页翻阅，轻点收放工具栏、横滑翻页，支持章末预读、缩放和进度恢复。
- **书架**：同一作品折叠多个来源，支持换源续读、自动已读、更新检查，以及记录和设置的 JSON 备份。
- **章节下载**：支持暂停、续下，离线准备完成后可断网重新打开已下载章节。
- **桌面与手机布局**：手绘小鱼、纸张配色和可暂停的封面墙。

各源的内容和可用性由原站决定；排行榜与更新能力因源而异。

## 开始使用

需要 **Python 3.11+** 和 [uv](https://docs.astral.sh/uv/)。部分源的章节解码及开发检查需要 **Node.js**。

```bash
git clone https://github.com/zzzfu411/Sardina-manga.git
cd Sardina-manga
uv sync --locked
uv run python server.py
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。macOS 完成依赖安装后，也可双击 [启动 Sardina.command](<启动 Sardina.command>)。

<details>
<summary>macOS / Linux 后台运行</summary>

```bash
uv run python scripts/sardina_service.py start
uv run python scripts/sardina_service.py status
uv run python scripts/sardina_service.py stop
```

日志保存在 `output/runtime/sardina-server.log`。启动器不会配置开机自启。

</details>

也可部署为 HTTPS 网站，配置见 [VPS 部署](docs/DEPLOYMENT.md)。

书架、设置和下载保存在当前浏览器，暂无账号与跨设备同步。首次联网后会准备离线资源，完成状态可在下载管理中查看；离线仅能阅读已下载内容。JSON 备份包含书架和设置，不包含图片。更换域名或浏览器时可导入备份，图片需重新下载。

## 接口

默认地址为 `http://127.0.0.1:8765`；公网部署需显式配置 HTTPS 域名。`POST` 请求使用 JSON，JSON 接口成功返回 `{"data": ...}`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/sites` | 获取已启用的漫画源 |
| `POST` | `/api/search` | 按关键词搜索，可指定来源 |
| `POST` | `/api/details` | 获取作品资料与章节目录 |
| `POST` | `/api/chapter-images` | 获取章节图片地址 |
| `GET` | `/api/image` | 代理封面或正文图片，返回图片字节 |
| `POST` | `/api/discovery` | 获取排行榜或最近更新 |
| `GET` | `/api/recommendations` | 获取推荐候选，本地偏好排序由前端完成 |

```bash
curl http://127.0.0.1:8765/api/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"三月的狮子","siteId":"mangabz"}'
```

参数、响应结构与其他端点见 [API 文档](docs/API.md)。书架与下载使用浏览器存储，没有对应的服务端管理 API。

## 开发

源适配在 [`client/`](client/)，页面与阅读器在 [`web/`](web/)，HTTP 路由在 [`server.py`](server.py)。

```bash
# 单元测试与前端语法检查
uv run python scripts/check.py

# 加上真实浏览器回归
npm install --global @playwright/cli@0.1.21
playwright-cli install-browser chrome
uv run python scripts/check.py --browser
```

检查结果写入 `output/checks/`。GitHub Actions 执行相同流程，并保留 `regression-evidence` 报告。

---

[开发记录](docs/) · [提交问题](https://github.com/zzzfu411/Sardina-manga/issues) · [项目起点](https://linux.do/t/topic/2919011)
