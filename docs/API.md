# HTTP API

[返回 README](../README.md)

以下接口对应默认的 `native` 模式，默认服务地址为 `http://127.0.0.1:8765`。默认只接受本机 Host；通过 `--public-origin https://manga.example.com` 可额外允许一个 HTTPS 域名。后端仍只监听回环地址，由反向代理提供 HTTPS。`POST` 如携带 `Origin`，其值必须与当前 Host 对应的本机地址或已配置的 HTTPS 地址一致；转发头不参与信任判断。`POST` 使用 `Content-Type: application/json`，请求体上限为 16 KiB。部署方法见 [VPS 部署](DEPLOYMENT.md)。

## 响应约定

除图片外，成功响应包装在 `data` 中，失败响应使用 `error`：

```json
{"data": {"images": ["https://image.mangabz.com/example.png"]}}
```

```json
{"error": "请输入 1–100 字的漫画名"}
```

上面的图片地址仅用于展示结构。参数错误通常返回 `400`，访问来源不匹配返回 `403`，上游请求或解析失败通常返回 `502`。搜索中的单源失败记录在该源的 `error` 字段，不会让整批搜索返回失败。

## 搜索与阅读

| 方法与路径 | 参数 | `data` 内容 |
| --- | --- | --- |
| `GET /api/sites` | 无 | 源数组，含 `siteId`、`siteName`，部分源附带 `notice` |
| `POST /api/search` | `keyword`：1–100 字；`siteId`：可选，省略时搜索全部已启用源 | 按源分组的数组，每组含 `siteId`、`siteName`、`results`、`elapsedMs`；失败组附带 `error` |
| `POST /api/details` | `siteId`、`detailUrl`；可选 `refresh: true` | 作品资料及 `chapters`；受限来源可能附带 `unavailableReason` |
| `POST /api/chapter-images` | `siteId`、`chapterUrl`；可选 `refresh: true` | `images` 数组，按阅读顺序排列 |
| `POST /api/book-metadata` | `siteId`、`detailUrl` | 轻量作品资料，不读取整本目录；仅部分源支持 |

调用顺序：从 `/api/sites` 取得 `siteId`，搜索结果中取得 `detailUrl`，详情的 `chapters[].url` 用作 `chapterUrl`。地址必须与对应来源匹配。

详情与章节接口可附带 `purpose`，取 `reader`（默认）、`background`、`prefetch` 或 `download`。当前阅读享有保留的请求容量；自动资料补全、预读和下载使用后台额度。

搜索结果中的作品通常包含 `title`、`detailUrl`、`coverUrl`、`author`、`latestChapter`、`siteId`、`siteName`。详情的章节条目包含 `name` 和 `url`，部分源附带分组、语言或序列信息；未提供的资料字段可能为空。

```bash
curl http://127.0.0.1:8765/api/sites

curl http://127.0.0.1:8765/api/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"三月的狮子","siteId":"mangabz"}'
```

### 图片代理

`GET /api/image` 返回图片字节，`Content-Type` 与实际图片类型一致。

| 查询参数 | 说明 |
| --- | --- |
| `url` | 图片地址，必须经过 URL 编码，且属于已允许的图片域名 |
| `siteId` | 图片所属源；应传入，以正确设置来源信息 |
| `purpose` | 可选：`reader`、`prefetch`、`download`、`cover`；默认为 `cover` |
| `retry` | 可选：传入非空值，例如 `1`，刷新服务端图片缓存 |

`reader` 表示当前阅读页，享有前台保留额度；预读、下载和封面使用后台额度。该接口不提供任意网址代理。

## 发现与推荐

| 方法与路径 | 参数 | `data` 内容 |
| --- | --- | --- |
| `GET /api/discovery/sources` | 无 | 各源支持的 `modes`、榜单 `periods` 和 `maxPage` |
| `POST /api/discovery` | `siteId`、`kind`；可选 `period`、`page`、`refresh` | `items`、`hasMore`、来源、列表类型与分页信息 |
| `POST /api/discovery/cover` | `siteId`、`detailUrl`；可选 `refresh: true` | 补取的作品封面信息；仅支持 `manhuagui` 与 `manben` |
| `GET /api/recommendations` | 查询参数 `batch`：0–31，默认 0；`refresh`：`0` 或 `1`，默认 `0` | `items`、`origins`、`warnings`、`batch`、`nextBatch`、`hasMore`、`candidateCount`、`fetchedAt` |
| `GET /api/home-sections` | 无 | 首页精选内容 |

发现列表的 `kind` 为 `popular` 或 `latest`。`page` 从 1 开始，`period` 取 `/api/discovery/sources` 中该源对应模式的值；无周期的模式省略 `period`。`POST` 的 `refresh` 是布尔值，与推荐接口查询参数的 `refresh=1` 不同。

```bash
curl http://127.0.0.1:8765/api/discovery \
  -H 'Content-Type: application/json' \
  -d '{"siteId":"manhuagui","kind":"popular","period":"week","page":1}'
```

推荐接口返回多源候选，作品可附带 `metadataAvailable` 与 `readingHealth`。前端根据本地书架、曝光和“不感兴趣”记录排序；这些偏好不会发送给后端。`nextBatch: null` 表示本轮没有下一批。

## 配置与源状态

| 方法与路径 | `data` 内容 |
| --- | --- |
| `GET /api/config` | 当前 `mode`、`sync`、`disabledSources` 与 `sourceCoverage` |
| `GET /api/source-catalog` | 源资料清单、接入状态、已启用源及其能力 |
| `GET /api/source-health` | 按源、按能力保存的请求观测结果，`scope` 为 `observed-requests` |
| `GET /api/offline-manifest` | 离线应用资源的内容版本与资源路径；不包含章节图片 |

健康状态来自本机实际请求，不代表所有作品或整个源站的可用率。源资料清单中的待适配条目也不等于已启用源。

书架、阅读设置、推荐反馈与章节下载由浏览器的 localStorage / IndexedDB 管理，不提供服务端增删改接口。

浏览器通过 `/sw.js` 缓存完整应用资源与源列表，供断网重新打开已下载章节使用；需要 HTTPS 或本机地址。搜索、目录和图片请求不会被该缓存长期保存，章节图片仍由下载管理负责。
