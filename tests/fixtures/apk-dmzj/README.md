# 动漫之家 / 再漫画适配样本

2026-09-21 使用公开接口采集，未发送账号、令牌、密码或客户端密钥。

| 文件 | 来源与性质 |
| --- | --- |
| `search-onepunch.json` | 真实 `/app/v1/search/index`，关键词「一拳」，14 条 |
| `search-empty-march.json` | 真实搜索「三月的狮子」，正常空结果 |
| `detail-restricted.json` | 真实 `/app/v1/comic/detail/42910?_v=2.2.5`，22 章，`canRead=false` |
| `chapter-restricted.json` | 真实 `/app/v1/comic/chapter/42910/73595?_v=2.2.5`，图片为空、`canRead=false` |
| `detail-public.json` | 真实 `/app/v1/comic/detail/86003?_v=2.2.5`，22 章，`canRead=true` |
| `chapter-public.json` | 真实 `/app/v1/comic/chapter/86003/182392?_v=2.2.5`，5 页，`canRead=true`；短效图片签名替换为 `fixture-redacted`，时间参数为 `0` |
| `chapter-authorized-synthetic.json` | 明确为合成样本，用于测试去重、页面顺序及权限优先于 URL 的行为；不是网络可用性证据 |

所有作品详情样本去除了与适配无关的上传者 `uid`。搜索与目录响应保留原生字段、分组及顺序，测试直接经过适配器解析。仅 HTTP 边界被替换为这些固定响应。

公开可读链路另有真实网络验证记录：`output/apk-sources/dmzj-validation.json`。旧动漫之家入口因证书主机不匹配、TLS 连接终止或 HTTP 502 未通过验证，因此不写成假成功 fixture，也不注册到 `SOURCES`。

协议证据来自本地 `Tachiyomi_ 再漫画_1.4.5.apk` 提取的 API host/path，以及当前公开扩展的 [Zaimanhua.kt](https://github.com/keiyoushi/extensions-source/blob/main/src/zh/zaimanhua/src/eu/kanade/tachiyomi/extension/zh/zaimanhua/Zaimanhua.kt) 和 [ZaimanhuaDto.kt](https://github.com/keiyoushi/extensions-source/blob/main/src/zh/zaimanhua/src/eu/kanade/tachiyomi/extension/zh/zaimanhua/ZaimanhuaDto.kt)。实现不包含登录或权限绕过逻辑。
