# 云漫 Intent → Spec → Impl 迭代计划

依据：[产品审计](2026-09-20_产品审计.md)。顺序：审计冻结 → 规格与接口冻结 → 三个 Agent 并行 → 主 Agent 集成 → 自动回归与真实浏览器验收 → 修复和交付。

## 1. Intent 与成功标准

输入「三月的狮子」后，准确作品位于首屏；同一本的繁简体和确认的数字别名可切源，相关作品独立显示。读者在作品卡片点章节进入阅读器，能在手机/桌面稳定阅读、跳页、找章节、恢复页内位置。源失败有可执行的恢复入口。

不把所有原始记录丢弃：显示作品数和候选记录数，保留其他匹配的展开入口。源未完成或出错明确呈现。搜索新词时旧响应不更新页面。

## 2. Spec：行为要求

### 2.1 搜索模型（S01/S02/Q01）

- NFKC 规范化，处理大小写、空格和常见标点；简繁映射明确受控，不宣称支持完整中文转换。
- 已知别名包含本例「三月的狮子 / 三月的獅子 / 3月的狮子 / 3月的獅子」，不把所有中文数字机械改写。
- 对齐原版已证实的海贼王/航海王、一拳超人/一击男等常见别名；使用精确别名表，不把名字中含「死神」的所有作品都合并成《死神》。
- 精确标题/已知别名优先于前后缀匹配，后者优先于弱相关；无关结果进入折叠的其他结果区。
- 相同规范名跨源聚合；版本标记（同人、外传、彩色、重制等）不被删除；非空作者存在明显冲突时保守拆组。
- 同一源同一 URL 去重；源优先级只在同一相关性层内起作用，禁止把大量弱匹配源置于精确作品之前。
- 聚合模型为纯函数，固定样本测试覆盖别名、标点、同名不同版本、作者冲突、空值、源顺序、渐进返回。

### 2.2 搜索卡片（S03/S04）

- 桌面宽卡片：封面 + 标题/作者/已知状态/简介，源切换按钮，章节预览网格（默认约 12 章）、目录正倒序、完整目录入口。
- 手机保持标题、源切换和章节可操作，320px 起无页面横向溢出；触控主要操作至少 40px 高。
- 只有可见及邻近卡片补详情，并发不超过 2；详情缓存按 siteId+detailUrl；同一搜索中源陆续返回不重置用户选择的源/章节排序。
- 详情失败显示重试和切源，搜索卡片基础信息仍可使用；切源后显示该源目录，不能残留上一源章节。
- 章节点击可携带已加载详情直接读，避免再等待一次目录请求。

### 2.3 数据与服务端（D01/D02/D03/D05）

- 保持现有 `/api/search`、`/api/details`、`/api/chapter-images` 请求格式与原生模式。
- 搜索条目至少包含 title/detailUrl/siteId/siteName；coverUrl/author/latestChapter/description/status 为有证据才填的可选字符串；不伪造状态。
- 元数据提取不额外向每条搜索结果发请求；优先复用已取得 HTML。解析范围约束在搜索结果项，排除导航、推荐和重复链接。
- 补充证据修正：DM5 的独立精确匹配 banner 也属于搜索结果项；必须保留，不可只读取相近结果的 h2.title。
- 详情响应维持 `{title,coverUrl,author,description,status,chapters,sourceUrl}`；chapters 每项 `{id,name,url,order,group}`，最早到最新。
- 图片代理补充 mhgui.com，域名边界、重定向校验、体积和类型限制继续生效。
- 精确开放本轮新增静态 JS/CSS 文件，不开放任意文件路径。

### 2.4 阅读器（R01–R05）

- 每次打开/切章拥有独立请求控制器和代际；旧结果、旧 img 事件不能更新新章节或书架。关闭、切章要中止请求、解绑观察器并清理计时器。
- 仅给当前页附近和视口内页分配图片请求，限制并发（建议 4），以尺寸/比例占位稳定长章布局；恢复后页时不先加载全部前页。
- 进度包含 chapterUrl/chapterName/page（0-based）/pageOffset（0–1）；按视口基准位置计算。兼容旧版仅 page 的书架。恢复和图片失败期间不能覆盖有效旧进度。
- 页码输入跳转、上一章/下一章、可搜索章节面板、章末下一章、明暗、宽度、沉浸模式；阅读偏好持久化。
- 页级重试、章节级重试、加载状态、末章/首章禁用正确；快捷键不拦截输入控件；Esc 先关闭章节面板/退出沉浸，再返回目录。
- 手机适配安全区，阅读宽度不产生横向滚动，工具栏不遮住漫画内容，沉浸有明确退出入口。
- 全局路由继续支持 `/s/:keyword`、`/m/:token`、`/read/:book/:chapter`；后退和刷新与进度保存一致。

## 3. 模块接口与文件归属（供并行实现）

使用原生 ES modules，根应用 `app.js` 改为 `type=module`。不引入前端框架或构建链。根 Agent 负责 package.json（type=module）与集成，团队不得交叉修改归属文件。

### Agent search

拥有 `web/search-model.js`、`web/search-view.js`、`web/search.css`、`tests/search*.test.js`；可新增本模块测试样本。

`createSearchView({root, api, imageUrl, onOpenBook, onReadChapter, onMetrics?})` 返回：

- `update({groups,keyword,filter})`：渲染 root，返回 `{rawCount,workCount,relatedCount,hiddenCount}`。groups 沿用 `{siteId,siteName,results,loading,error}`，filter 是单源 id 或空串。
- `reset()`：新搜索/离开搜索时取消详情和清理当前选择；`destroy()` 可完整销毁。
- 回调 `onOpenBook(book, detail?)`、`onReadChapter(book, chapter, detail)`。detail 是已加载的完整详情，可供根应用直接使用。
- 集成审查补充：详情揭示新的有效作者后重新聚合，避免同名异作者误合并；可选 `onMetrics(metrics)` 同步更新统计，根应用只更新状态行，避免回调递归刷新。
- `api(path,body,signal)` 返回 payload.data；`imageUrl(url,siteId)` 返回图片代理 URL。
- root 仅是 `#result-grid`；源筛选和搜索进度在根应用管理。

### Agent reader

拥有 `web/reader.js`、`web/reader-model.js`（如需要）、`web/reader.css`、`tests/reader*.test.js`。

`createReader({root,api,imageUrl,getProgress,onProgress,onNavigate,onExit,toast})` 自行构造 `#reader-dialog` 内部 DOM，返回：

- `open({book,chapters,index,resume=false,push=true})`（可以 async），`close()`（静默，保存并清理，不触发 onExit），`flush()`、`isOpen()`。
- `getProgress(book)` 返回 shelf 中记录或 undefined；`onProgress(book, progress)` 只提交当前章有效进度。
- `onNavigate(book, chapter, {push})` 在切章时通知路由；`onExit()` 由退出按钮/Esc 触发，根应用再调用 close 并展示详情。
- 根应用控制详情弹窗和路由；阅读器控制自己的 cancel/keyboard/visibilitychange/pagehide。
- 对外使用页码 0-based，UI 显示 1-based；pageOffset 归一到 0–1。

### Agent data

拥有 `client/`、`server.py`、`tests/test_application.py`、新增 Python adapter tests/fixtures。修复数据提取、必要元数据、封面白名单并测兼容性。

新增静态路由精确允许 `/search-model.js`、`/search-view.js`、`/search.css`、`/reader.js`、`/reader-model.js`、`/reader.css`。不修改 web/app.js/index.html/base style 或其他团队测试。

### 主 Agent

拥有 `web/app.js`、`web/index.html`、`web/style.css`、`package.json`、README、docs、浏览器验证产物和脚本。负责搜索状态、路由、书架迁移、模块集成与视觉校正。模块接口变化先协商后集成。

## 4. 实施里程碑

| 阶段 | 工作及依赖 | 完成条件 |
| --- | --- | --- |
| M0 | 审计与规格（本文件及审计） | 证据/推断分离、问题编号和契约明确，随后才启动团队 |
| M1 | 三 Agent 并行：搜索模型/卡片、阅读器、源数据 | 各自模块、针对性测试、使用说明；主 Agent 同步完成 app 集成 |
| M2 | 合并接口与状态 | 新搜索、切源、点章、后退、书架继续阅读走通；无旧代码重复监听 |
| M3 | 自动回归 | Python 现有测试和新增解析/白名单测试通过；JS 搜索/进度模型回归通过；模块语法正确 |
| M4 | 真实浏览器验证 | 桌面 1200px、手机 390px/小屏320px；同关键词、目录、页码、明暗、沉浸、恢复、失败重试和竞态 |
| M5 | 修复与交付 | 问题复测，README更新，验收报告列出实际证据/实时源限制；Goal 完成 |

## 5. 验收矩阵

| 编号 | 输入 / 操作 | 预期 |
| --- | --- | --- |
| A01 | 固定「三月的狮子」混合样本，源乱序返回 | 主作品首位，三月/3月/獅别名合并，无关结果不占首屏 |
| A02 | 同人/外传/彩色版及不同作者同名 | 不与主作品误合并；相关结果可展开 |
| A03 | 原版原始响应回放及本地实时同词搜索 | 显示聚合作品数/原始数；可见卡片有源按钮和目录；记录实际可用源 |
| A04 | 卡片切源、倒序后其他源返回 | 已选源和排序保持；点击章节进入正确源正确章 |
| A05 | 搜索 A 立即搜索 B；详情延迟返回 | 只出现 B 结果；详情并发≤2，旧详情不污染新卡片 |
| A06 | 支持的封面 CDN 与非法重定向 | mhgui 封面允许；无关域/伪造后缀/本机跳转仍拒绝 |
| A07 | 长章恢复到后段 | 页码和偏移恢复，前面全部页不同时请求 |
| A08 | 快速切章/关闭时旧请求迟到 | 当前内容和保存进度不被旧章改变 |
| A09 | 单页失败、整章失败 | 可独立重试；已有进度不被失败重置；按钮状态正确 |
| A10 | 跳页、找章节、前后章、明暗/宽度/沉浸 | 操作生效；首尾边界正确；Esc/键盘及触控可用 |
| A11 | 刷新阅读链接、退出后从书架继续、浏览器后退 | 正确作品/源/章节/页内位置；旧书架仍兼容 |
| A12 | 1200/390/320px | 无页面横向溢出，关键操作无遮挡，截图检查文字与层次 |

自动测试应验证行为边界而非重复实现；真实源网络失败不能用虚构数据冒充成功。受控样本验证竞态/错误时，报告明确标注为模拟。

## 6. 风险与回退

源站 HTML/CDN 可随时变化，新增解析使用样本回归，接口失败孤立到单源。别名表保持小而可解释；不能证明的异名保留独立。详情请求增多通过可视加载与缓存限制。阅读器性能变化用长章模拟记录实际图片请求数量。前端模块不改既有 API 与书架 key，必要时可回退模块引用，原数据继续可读。

实施状态与实际结果将写入独立验收报告，不把本计划中的目标作为已交付事实。

## 7. 实施状态（2026-09-20）

M0–M5 已完成。三 Agent 交付后完成主应用集成、一次作者证据边界补强、65 项自动回归、真实浏览器与实时源验收。实际结果、截图和尚存边界见[迭代验收报告](2026-09-20_迭代验收.md)。
