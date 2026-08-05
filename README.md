# Hana 插件需求墙

面向社区群的插件「需求 + 成果」交换墙：群友发需求、留言认领、提交成果（GitHub 仓库），成果自动绑定需求。零依赖、单文件后端，部署简单。

## 功能

- **QQ 账号登录**：右上角「👤 未登录」登录/注册。QQ 号 + 自设密码（密码加盐哈希存储，不存明文）。**不登录只能浏览，不能留言、点赞**；改名后历史留言/点赞/墙留言自动更新为新昵称
- **旧游客数据继承**：老用户注册时填的昵称若与旧游客身份匹配（昵称+QQ 或昵称+设备指纹双重验证，防冒名），自动继承原身份（👑 标记、违规记录、历史点赞关联）
- **需求 · 委托（左栏）**：发布需求帖；卡片下留言认领（可互相回复）；「提交成果」一键发布成果并自动绑定该需求（需求随之移入成果区）
- **成果（右栏）**：GitHub 只填仓库名（如 `xiaohong/weather-hana`），自动生成链接、自动从 GitHub API 拉取仓库描述（描述留空时）；手动填描述则优先；成果卡同样可以留言、回复；「📋 复制仓库名」一键复制仓库名，方便粘贴给智能体安装
- **点赞**：需求 / 成果卡片均可点赞，再点一下取消；悬停可见谁赞过（留名）；用浏览器指纹（设备特征哈希）防同一设备反复刷赞
- **留言板**：顶部「💬 留言板」标签页，站内自由讨论，留言可互相回复
- **管理**：页脚「⚙ 管理」输入口令进入管理模式（令牌持久保存，重开浏览器不用重复输入），可编辑 / 删除卡片；编辑和删除都会记录到「📜 操作日志」（仅管理员可见）；管理模式与登录账号独立
- **账号管理（管理端）**：重置任意账号密码（忘记密码找管理员人工重置）、授予/撤销管理员（想当管理员需向管理员申请）
- 群名固定显示（可在 `server.py` 的 `GROUP_NAME` 修改）
- 米色素雅主题，桌面双栏、移动端单列

## 技术栈

- 后端：Python 3 标准库（`http.server`），零第三方依赖，单文件 `server.py`
- 前端：原生 HTML / CSS / JS，无框架、无构建
- 存储：`data.json`（帖子）+ `logs.json`（操作日志）+ `likes.json`（点赞指纹昵称）+ `wall.json`（留言板）+ `users.json`（账号，密码加盐哈希）+ `visitors.json`（旧游客数据，注册继承后自动清除），均为原子写入 + 线程锁；换机器 = 拷贝这些文件
- 数据目录：默认与 `server.py` 同目录，可用环境变量 `HANA_WALL_DATA_DIR` 指定（本地测试与生产数据分离）

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
logs.json          # 运行时生成：操作日志（不入库）
likes.json         # 运行时生成：点赞指纹（不入库）
wall.json          # 运行时生成：留言板（不入库）
users.json         # 运行时生成：QQ 账号（密码加盐哈希，不入库）
visitors.json      # 运行时生成：旧游客数据，注册继承后自动清除（不入库）
secret.txt         # 运行时创建：管理口令（不入库）
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/posts?fp=设备指纹` | 全部帖子（附点赞数、本设备是否已赞） |
| POST | `/api/posts` | 发帖（need/done，需登录） |
| POST | `/api/posts/{id}/comments` | 卡片留言（`reply_to` 可回复指定留言，需登录） |
| POST | `/api/posts/{id}/like` | 点赞 / 取消（需登录） |
| GET | `/api/wall` | 留言板全部留言 |
| POST | `/api/wall` | 发留言板留言（`reply_to` 可回复，需登录） |
| POST | `/api/user/register` | 注册（QQ + 密码 + 昵称；昵称匹配旧游客数据自动继承身份） |
| POST | `/api/user/login` | 登录（QQ + 密码；失败限流 5 分钟 5 次） |
| POST | `/api/user/logout` | 登出（作废凭证） |
| POST | `/api/user/update` | 修改昵称（需登录） |
| GET | `/api/user/me?token=` | 当前登录信息 |
| POST | `/api/admin/login` | 口令换令牌 |
| POST | `/api/admin/user/reset_password` | 重置指定 QQ 的密码（需令牌，记日志） |
| POST | `/api/admin/user/set_admin` | 授予/撤销管理员（需令牌，记日志） |
| POST | `/api/admin/posts/{id}/edit` | 编辑（需令牌，记日志） |
| POST | `/api/admin/posts/{id}/delete` | 删除（需令牌，记日志） |
| POST | `/api/admin/posts/{id}/sink` | 沉底/恢复（需令牌，记日志） |
| POST | `/api/admin/posts/{pid}/comments/{cid}/hide` | 屏蔽/恢复帖子评论（需令牌，记日志，原地留痕） |
| POST | `/api/admin/wall/{mid}/hide` | 屏蔽/恢复留言板留言（需令牌，记日志，原地留痕） |
| GET | `/api/admin/logs?token=` | 操作日志（需令牌） |

## 安全说明

- 管理口令存放在服务器上的 `secret.txt`（建议 `chmod 600`），**不进代码仓库**
- 账号密码使用 pbkdf2_hmac（sha256，20 万次迭代 + 随机盐）哈希存储，**不存明文**；登录失败按 QQ 限流（5 分钟 5 次）、注册按设备限流（1 小时 3 个）
- 登录后颁发随机令牌（服务重启不失效）；登出/重置密码后凭证作废
- 旧游客身份继承需「昵称+QQ」或「昵称+设备指纹」双重验证，管理员标记不会被同名人冒领
- 口令比对使用 `hmac.compare_digest` 防时序攻击
- 点赞记录的是**设备指纹**（浏览器特征哈希，非个人信息），仅用于防刷，不可逆向出真实设备
- 建议定期备份 `data.json`（连同 `logs.json` / `likes.json` / `wall.json` / `users.json`）

## License

MIT
