# COLAMANGA 域名迁移与阅读链路复核

复核日期：2026-09-21。目标是找到当前官方入口，并验证云漫能否完成搜索 → 真实章节目录 → 图片列表 → 本地代理解码 → 阅读器显示。

**结论：迁移属实，当前官网是 https://www.yoyomanga.com；本轮仍未打通阅读链路。** 网页能搜索作品，但实测目录和阅读页均指向 App 专属内容。进一步取得官网提供的 2.1.1 APK，找到了 App 配置入口，却尚未还原可正常取得配置的游客请求协议。不能通过替换域名把它标成已接入，也不能据此断言 App 接口永远无法适配。

**后续解包复核：** APK 完整性、标准签名、独立 Manifest / DEX 解析均通过；AOT 原始 ASCII 扫描发现少量粘连与噪声，已补充边界审计。两个完整配置 URL 保持有效，但本轮没有完成 Dart 调用链还原，普通 GET 的 403 不能证明 App 的正确请求方式不可用。详见 [APK 解包独立复核](2026-09-21_APK解包独立复核.md)。

## 1. 域名核验

| 入口 | 本轮结果 |
| --- | --- |
| `https://www.colamanga.com/` | 301 → `https://www.yoyomanga.com/`，终点 200 |
| `https://www.cocomanga.com/` | 301 → 同一新站，终点 200 |
| [官方导航](https://www.acloudmerge.com/) | 唯一漫画入口仍指向 `www.yoyomanga.com` |
| [新站首页](https://www.yoyomanga.com/) | 保留 COLAMANGA 品牌，并提供官方 App 下载入口 |

没有找到官方公布的更新漫画域名。`cocomanga.org`、其他同名“一本漫画”规则不能仅凭名称视为此站迁移后的服务。

HTTP 状态、跳转链和 HTML 哈希见 [website-probes.json](../output/colamanga-recheck-20260921/website-probes.json)；导航与公开维护记录见 [extension-research.json](../output/colamanga-recheck-20260921/extension-research.json)。

## 2. 使用当前新域名复测作品

| 样本 | 搜索 / 详情 | 真实网页章节 |
| --- | --- | --- |
| 全知读者视角 | 搜到普通版与简体版，共 2 条 | 两个目录均只有 App 导流项，0 个可读章节 |
| 斗破苍穹 | 搜到 1 条 | 目录只有 App 导流项，0 个可读章节 |
| 一拳超人 G(三方) | 原有作品详情可访问 | 目录为 App 导流项；实际阅读页显示 App 专属提示 |
| 三月的狮子 | 本次该字面查询返回 0 条 | 未进入目录；这不证明 App 内没有此作品 |

在 Chromium 中正常加载《一拳超人 G(三方)》[阅读页](https://www.yoyomanga.com/manga-gm397966/1/1.html)，站点阅读脚本 `__cr` 已加载，但正文显示“App专属内容”，并说明“web端暂不考虑放出”。`#mangalist img` 数量为 **0**。本次浏览器检查禁用了图片和媒体网络请求；结论来自站点生成的页面文案和未创建正文图片节点，不是把被拦下的图片请求当作加载失败。

原 HTML 仍含旧加密数据和部分章节变量，不代表站点当前提供可阅读图片。前轮旧图地址的 404 也没有冒充本轮重新下载验收。

证据：[作品抽样](../output/colamanga-recheck-20260921/ordinary-work-probes.json)、[浏览器观察](../output/colamanga-recheck-20260921/browser-app-only.json)、[实际阅读页截图](../output/colamanga-recheck-20260921/browser-app-only.png)。

## 3. 官网 App 与本地 APK 的差别

本地 174 个 APK 文件中有 4 份相关扩展，对应 2 个不同版本：COLAMANGA / 一本扩展 1.4.18、1.4.22。它们使用 HTML、`manga.read.js` 和 WebView 取图，并不是当前官方 App 的业务接口。

从[官网下载页](https://www.yoyomanga.com/appDownloadPage.html)取得的 Android 包显示名称“漫城”、版本 **2.1.1 / 211**，使用 Flutter arm64 AOT。APK SHA-256 为 `960c90c3431eaa4ead58018bf01adf4773f0fd88968fc5fef1b1bef5ecf51304`。本轮只做静态分析，没有安装或运行该 APK。

已确认的完整配置 URL：

- `https://apibase.colamanga.com/getAppBaseData`
- `https://apibase.acloudmerge.com/getAppBaseData`

两个地址在本机普通无凭据 GET 和 Chromium 正常导航中均返回 **403 / Cloudflare HTML**，没有返回配置 JSON。页面正文提到 `vipdownload.top`，但浏览器最终 URL 仍是请求的配置地址；这不是新官网域名的证据。APK 中存在游客界面文案，因此不能把这次 WAF 拦截解释为“必须登录才能阅读”。

静态字符串还含 `/search`、`/comicDetail`、`/mangaPageDetail` 以及 `apiBaseUrl`、`imageServers`、`pageList` 等名称，但其中混有 Flutter 内部路由。尚未恢复各请求的方法、参数和调用关系，也没有收到真实业务 JSON，不能直接拼接这些名称并声称已恢复协议。证书、签名等字段的存在同样不能单独证明每个接口强制使用它们。

证据：[APK 分析汇总](../output/colamanga-recheck-20260921/apk-research-summary.json)、[静态证据](../output/colamanga-recheck-20260921/apk-research-official-static.json)、[普通请求结果](../output/colamanga-recheck-20260921/app-bootstrap-probes.json)、[浏览器配置请求结果](../output/colamanga-recheck-20260921/browser-bootstrap.json)。

## 4. 公开扩展维护记录交叉核验

Keiyoushi 于 **2026-02-28** 合并 [Remove ColaManga #13621](https://github.com/keiyoushi/extensions-source/pull/13621)，删除的最后版本为 1.4.26。[App API 请求 #11099](https://github.com/keiyoushi/extensions-source/issues/11099)仍开放；本轮读取全部 29 条评论、55 个关联事件，没有找到关联的已实现 App 适配器。当前扩展目录也未重新列出该源。

ComicCrawler 等项目保留的旧 HTML 解析器不能作为现在可读的证据。上述维护记录只用于交叉核验，本轮结论仍以真实站点和官方 APK 的直接检查为准。

## 5. 云漫中的实际变更

- 源目录的 `vomic:21`、`tachiyomi:1`、`tachiyomi:27` 均更新为已证实的新域名，并说明网页 App 专属、App 阅读链路未验证。
- COLAMANGA 插件条目由“待适配”改为“暂不可用”；一本扩展保留同源重复标记，不多算一个源。
- 更新候选适配器提示和 README；保留原始提取资料，审计记录保存旧域名。
- **实际搜索源仍为 25 个**。137 条资料的状态计数为：已接入 10、重复入口 20、暂不可用 26、待适配 81。资料条目与实际源不是一一对应关系。

本次没有注册不能阅读的 COLAMANGA 适配器，也没有把 App 导流链接显示成漫画章节。

验证：候选 HTML 解析器与源目录的 **12 项现有回归检查全部通过**；运行中的 8765 服务返回新域名及正确计数；浏览器搜索 `colamanga` 显示 3 条资料，其中 2 条暂不可用、1 条同源重复。检查记录见 [validation.json](../output/colamanga-recheck-20260921/validation.json)。这些检查只证明本次状态更新正确，不代表 COLAMANGA 阅读链路通过。

## 6. 下一次接入所需的具体证据

1. 恢复当前官方 App 正常游客启动时的配置请求，明确方法、参数及响应格式，实际取得有效配置。
2. 从真实调用关系确认搜索、目录、页列表的网络地址，使用普通作品逐步检查作品身份、章节顺序和总页数；不能把内部路由名称当接口。
3. 通过云漫图片代理完整解码一张真实正文图片，再验收阅读器、切章和失败提示。全部通过后再注册源并改为“已接入”。

本轮停留在第 1 步：找到了入口，但没有恢复可工作的请求协议。未取得目录、页列表和真实图像，因此阅读验收不通过。机器可读结论及证据索引见 [audit.json](../output/colamanga-recheck-20260921/audit.json)。
