# Hana 插件需求墙

面向社区群的插件「需求 + 成果」交换墙：群友发需求、留言认领、提交成果（GitHub 仓库），成果自动绑定需求。零依赖、单文件后端，部署简单。

## 功能

- **需求 · 委托（左栏）**：发布需求帖；卡片下留言认领（昵称选填，可匿名）；「提交成果」一键发布成果并自动绑定该需求（需求随之移入成果区）
- **成果（右栏）**：GitHub 只填仓库名（如 `xiaohong/weather-hana`），自动生成链接、自动从 GitHub API 拉取仓库描述（描述留空时）；手动填描述则优先
- **管理**：页脚「⚙ 管理」输入口令进入管理模式，可编辑 / 删除卡片；令牌存浏览器会话，可随时退出
- 群名固定显示（可在 `server.py` 的 `GROUP_NAME` 修改），无注册、开放浏览
- 米色素雅主题，桌面双栏、移动端单列

## 技术栈

- 后端：Python 3 标准库（`http.server`），零第三方依赖，单文件 `server.py`
- 前端：原生 HTML / CSS / JS，无框架、无构建
- 存储：`data.json`（原子写入 + 线程锁），换机器 = 拷贝一个文件

## 本地运行

```bash
# 1. 设置管理口令（可选，不设则无法进入管理模式）
printf '你的口令' > secret.txt

# 2. 启动（默认端口 3000）
python3 server.py
# 自定义端口
PORT=8080 python3 server.py
```

浏览器打开 http://127.0.0.1:3000

## 部署（服务器）

以 systemd 为例（`/etc/systemd/system/hana-wall.service`）：

```ini
[Unit]
Description=Hana Plugin Wall
After=network.target

[Service]
WorkingDirectory=/root/www/hana-plugin-wall
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=3
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now hana-wall
```

## 目录结构

```
server.py          # 后端：静态文件 + API + 存储
static/
  index.html       # 页面
  style.css        # 米色主题
  app.js           # 前端逻辑
data.json          # 运行时生成：所有帖子数据（不入库）
secret.txt         # 运行时创建：管理口令（不入库）
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/posts` | 全部帖子 |
| POST | `/api/posts` | 发帖（need/done） |
| POST | `/api/posts/{id}/comments` | 需求卡片留言 |
| POST | `/api/admin/login` | 口令换令牌 |
| POST | `/api/admin/posts/{id}/edit` | 编辑（需令牌） |
| POST | `/api/admin/posts/{id}/delete` | 删除（需令牌） |

## 安全说明

- 管理口令存放在服务器上的 `secret.txt`（建议 `chmod 600`），**不进代码仓库**
- 登录后颁发随机令牌，仅存内存，服务重启即失效
- 口令比对使用 `hmac.compare_digest` 防时序攻击
- 建议定期备份 `data.json`

## License

MIT
