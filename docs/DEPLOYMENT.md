# VPS 部署

Sardina 的页面和 API 由同一个 Python 服务提供，没有前端构建步骤。推荐使用 Nginx 提供 HTTPS，systemd 管理应用进程。

## 运行结构

```text
浏览器 → HTTPS / Nginx → 127.0.0.1:8765 / Sardina → 漫画源
```

仓库附带 `manga.yeuxark.com` 的 [Nginx 配置](../deploy/nginx-manga.conf) 和 [systemd 单元](../deploy/sardina.service)。部署到其他域名时，同时替换这两份文件中的域名和证书路径。

| 项目 | 路径 |
| --- | --- |
| 版本目录 | `/opt/sardina/releases/<版本>/` |
| 当前版本软链接 | `/opt/sardina/current` |
| 漫画源健康状态 | `/var/lib/sardina/source-health.json` |
| Nginx 站点配置 | `/etc/nginx/sites-available/manga.yeuxark.com` |
| 证书验证目录 | `/var/lib/sardina-acme` |
| 应用日志 | `journalctl -u sardina` |

后端始终监听回环地址，不需要开放 8765 端口。`--public-origin` 只允许指定域名，并按 HTTPS 地址检查浏览器的 `Origin`。默认不传参数时仍保持本机使用方式。

## 安装

需要 Python 3.11+、Node.js、Nginx、Certbot 和 systemd。Ubuntu 24.04 的 Python 3.12 可直接使用；部分漫画源需要 Node.js 解码章节。

1. 将域名解析到服务器，确认 80 和 443 端口可访问。如果使用 Cloudflare，源站部署完成后应使用 Full (strict) HTTPS。
2. 将仓库代码放入新版本目录。在本地从锁文件导出依赖，连同代码上传；不上传 `.env`、本机书架、下载图片或 `output/`：

   ```bash
   uv export --locked --no-dev --no-emit-project --format requirements.txt -o requirements.lock.txt
   ```

   在服务器的新版本目录运行：

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install --require-hashes -r requirements.lock.txt
   ```

3. 创建专用服务用户，将 `current` 软链接指向该版本。代码和虚拟环境由 root 管理；应用通过 systemd 的 `StateDirectory` 写入自己的状态目录：

   ```bash
   useradd --system --user-group --home-dir /var/lib/sardina --shell /usr/sbin/nologin sardina
   ln -s /opt/sardina/releases/<版本> /opt/sardina/current
   install -m 644 /opt/sardina/current/deploy/sardina.service /etc/systemd/system/sardina.service
   systemctl daemon-reload
   systemctl enable --now sardina
   curl --fail http://127.0.0.1:8765/api/config
   ```

4. 先新增只监听 80 的站点，`server_name` 为目标域名。为 `/.well-known/acme-challenge/` 配置 `root /var/lib/sardina-acme`，校验配置后重载 Nginx，再申请独立证书：

   ```bash
   mkdir -p /var/lib/sardina-acme
   certbot certonly --webroot -w /var/lib/sardina-acme \
     -d manga.yeuxark.com --cert-name manga.yeuxark.com
   ```

5. 证书成功后安装仓库中的完整 Nginx 配置。它保留 HTTP/HTTPS 证书验证路径，将其他 HTTP 请求转到 HTTPS：

   ```bash
   install -m 644 /opt/sardina/current/deploy/nginx-manga.conf /etc/nginx/sites-available/manga.yeuxark.com
   ln -s /etc/nginx/sites-available/manga.yeuxark.com /etc/nginx/sites-enabled/manga.yeuxark.com
   nginx -t && systemctl reload nginx
   ```

   若第 4 步已创建软链接，跳过 `ln`。配置 Certbot 的证书续期 deploy hook，成功续期后执行 `nginx -t && systemctl reload nginx`，并确认 `certbot.timer` 已启用。

## 更新和回退

将新版本放入另一个 `releases/` 目录，在其中安装锁定依赖并运行检查。记录 `readlink -f /opt/sardina/current`，将软链接切到新版本后执行 `systemctl restart sardina`。若健康检查失败，将链接切回原版本并重启。状态目录和浏览器数据不随版本目录替换。

修改 Nginx 前保留旧配置；只有 `nginx -t` 成功才执行 reload。不要覆盖其他站点配置。应用设置了崩溃重启、384 MiB 内存上限；Nginx 限制请求体和并发连接，图片及元数据请求仍受应用内部并发预算约束。

## 验证

```bash
systemctl is-active sardina
systemctl is-enabled sardina
curl --fail https://manga.yeuxark.com/api/config
curl --fail https://manga.yeuxark.com/api/search \
  -H 'Origin: https://manga.yeuxark.com' \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"三月的狮子","siteId":"mangabz"}'
```

还应在浏览器检查搜索 → 详情 → 章节 → 图片，确认深链接刷新有效、跨站 `Origin` 返回 403。漫画源受地区和原站限制，注册的源不代表都能从服务器访问。

## 数据归属

网站没有账号系统。书架、阅读进度、推荐偏好和下载图片仍保存在访问者自己的浏览器。不同浏览器、设备、域名分别存储；从 `localhost` 迁移到公网域名时，先导出书架，再在新网站导入。JSON 备份不含下载图片。

联网完成离线准备后，可以断网重新打开已下载章节；状态可在下载管理中查看。应用资源通过 Service Worker 缓存，HTTPS 部署无需额外配置；新版本资源完整缓存后用于下一次导航，不中断当前阅读。共享服务器只保存源健康状态及内存缓存，不会同步个人书架。
