#!/usr/bin/env python3
"""Hana 插件需求墙 - 单文件服务（仅用 Python 标准库，无第三方依赖）

- 静态页面：static/ 目录
- 数据存储：data.json / logs.json / likes.json / wall.json / users.json（默认同目录，可用 HANA_WALL_DATA_DIR 指定目录）
- 账号体系：QQ 号 + 自设密码（pbkdf2 加盐哈希），旧游客数据（visitors.json）注册时按昵称继承
- 端口：默认 3000，可用环境变量 PORT 覆盖
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("HANA_WALL_DATA_DIR", BASE_DIR)
DATA_FILE = os.path.join(DATA_DIR, "data.json")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")
LIKES_FILE = os.path.join(DATA_DIR, "likes.json")
WALL_FILE = os.path.join(DATA_DIR, "wall.json")
VISITORS_FILE = os.path.join(DATA_DIR, "visitors.json")
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
ANNOUNCEMENT_FILE = os.path.join(DATA_DIR, "announcement.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")  # QQ 账号体系：qq -> 用户记录（密码哈希+昵称+fp+is_admin+flags）
STATIC_DIR = os.path.join(BASE_DIR, "static")
SECRET_FILE = os.path.join(BASE_DIR, "secret.txt")
SENSITIVE_FILE = os.path.join(BASE_DIR, "sensitive.txt")  # 敏感词库（本地化，一行一词）
TRASH_FILE = os.path.join(DATA_DIR, "trash.json")  # 回收站（软删除，定期清理）

GROUP_NAME = "Hana 交流群"
TOKENS = set()

TZ = timezone(timedelta(hours=8))  # 北京时间
LOCK = threading.Lock()

MAX_LEN = {
    "title": 100,
    "content": 2000,
    "author": 50,
    "contact": 100,
    "name": 50,
    "note": 200,
    "group": 50,
    "github": 300,
    "password": 100,
    "token": 100,
    "comment": 200,
    "fp": 64,
    "wall_content": 500,
    "wall_name": 50,
    "announcement": 500,
    "qq": 20,
}

MAX_LOG = 500  # 操作日志最多保留条数
MAX_VISITORS = 1000  # 游客账号最多保留数（超出删最早）
TRASH_RETENTION_DAYS = 7  # 回收站保留天数，超期自动永久清理（懒清理：读取时）
LOGIN_LOCK_WINDOW = 300  # 登录失败限流窗口（秒，5 分钟）
LOGIN_MAX_FAILS = 5  # 窗口内最多失败次数，超限锁定
LOGIN_FAILS = {}  # qq -> [失败时间戳]，登录失败限流（防暴力破解）
REGISTER_WINDOW = 3600  # 注册限流窗口（秒，1 小时）
REGISTER_MAX = 3  # 同一设备指纹 1 小时内最多注册账号数
REGISTER_TIMES = {}  # fp -> [注册时间戳]，防批量注册

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def load_secret():
    env = os.environ.get("HANA_WALL_PASSWORD", "").strip()
    if env:
        return env
    if os.path.isfile(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


# ---------- 敏感词过滤（Trie 前缀树，纯标准库实现） ----------
# 状态三态：normal（正常）/ pending（命中敏感词，待管理员审核）/ hidden（管理员屏蔽）

TRIE = None
END = "\x00"

# 白名单：插件开发场景的高频正常词（词库来自腾讯游戏运营场景，混入了大量技术/运营词，
# 命中这些直接忽略，避免误伤；管理员审核机制兜底漏检）
DEFAULT_WHITELIST = {
    "管理", "管里", "管理员", "服务管理", "服务器", "服务", "官方", "维护", "系统",
    "系统公告", "公告", "客户服务", "客户", "客服", "服务天使", "助理", "辅助程序",
    "测试", "游戏", "游戏管理员", "运营", "运营者", "运营组", "运营商", "运营长",
    "运营官", "运营人", "审查", "巡查", "监督", "监管",
    "http", "https", "com", ".com", "test", "admin", "system", "game", "master",
    "gm", "client", "server", "cs", "kefu",
}

# URL 剥离：链接里的 http/.com 等垃圾词条不该触发拦截
SENSITIVE_URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]，。！？、；：《》]+")


def load_sensitive():
    """启动时加载敏感词库构建 Trie；跳过单字词（误伤率高）与空行，返回有效词条数"""
    global TRIE
    root = {}
    count = 0
    if not os.path.isfile(SENSITIVE_FILE):
        TRIE = root
        return 0
    try:
        with open(SENSITIVE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if len(word) < 2:
                    continue
                node = root
                for ch in word:
                    node = node.setdefault(ch, {})
                node[END] = True
                count += 1
    except Exception:
        root = {}
        count = 0
    TRIE = root
    return count


def find_sensitive(text):
    """返回文本中命中的敏感词列表（去重，按首次出现顺序；URL 与白名单词不计）"""
    if not TRIE or not text:
        return []
    text = SENSITIVE_URL_RE.sub(" ", text)
    hits = []
    seen = set()
    n = len(text)
    for start in range(n):
        node = TRIE
        for j in range(start, n):
            ch = text[j]
            if ch not in node:
                break
            node = node[ch]
            if END in node:
                word = text[start:j + 1]
                if word not in seen:
                    seen.add(word)
                    hits.append(word)
    return [w for w in hits if w.casefold() not in DEFAULT_WHITELIST]


def item_status(item):
    """兼容新旧数据：status 字段优先，旧数据 hidden=true 视为 hidden"""
    if not item:
        return "normal"
    st = item.get("status")
    if st in ("normal", "pending", "hidden"):
        return st
    return "hidden" if item.get("hidden") else "normal"


def set_status(item, status):
    """写 status 并清理旧 hidden 字段，保证双轨不并存"""
    item["status"] = status
    item.pop("hidden", None)


# ---------- 回收站（软删除：删除进回收站，超期自动清理，可恢复/彻底删除） ----------

def load_trash():
    """读取回收站并顺手清理过期条目（懒清理：删除/查看时触发）"""
    trash = load_json(TRASH_FILE, [])
    now = datetime.now(TZ)
    keep = []
    for t in trash:
        try:
            dt = datetime.strptime(t.get("deleted_at", ""), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except Exception:
            dt = now
        if (now - dt).days < TRASH_RETENTION_DAYS:
            keep.append(t)
    if len(keep) != len(trash):
        save_json(TRASH_FILE, keep)
    return keep


def save_trash(trash):
    save_json(TRASH_FILE, trash)


def move_to_trash(kind, pid, data, title, who):
    """把内容快照移入回收站，返回 tid"""
    trash = load_trash()
    tid = max((t.get("tid", 0) for t in trash), default=0) + 1
    trash.append({
        "tid": tid, "kind": kind, "pid": pid,
        "data": data, "title": title,
        "who": who, "deleted_at": now_str(),
    })
    save_trash(trash)
    return tid


def check_token(token):
    return bool(token) and token in TOKENS


def load_tokens():
    """启动时从文件加载管理令牌（重启不失效）"""
    global TOKENS
    TOKENS = set(load_json(ADMINS_FILE, []))


def save_tokens():
    save_json(ADMINS_FILE, sorted(TOKENS))


def check_visitor(token):
    """校验登录凭证，有效返回昵称，无效返回 None"""
    info = get_visitor(token)
    return info.get("name") if info else None


def get_visitor(token):
    """校验登录凭证，有效返回用户记录 dict（含 fp/name/is_admin），无效返回 None"""
    if not token:
        return None
    users = load_json(USERS_FILE, {})
    for info in users.values():
        if info.get("token") == token:
            return info
    return None


def visitor_map():
    """设备指纹 -> {name, is_admin}（用于显示时归一化 + 管理员标签）"""
    users = load_json(USERS_FILE, {})
    m = {}
    for info in users.values():
        if info.get("fp"):
            m[info["fp"]] = {
                "name": info.get("name", ""),
                "is_admin": bool(info.get("is_admin")),
            }
    return m


# ---------- 账号体系（QQ + 自设密码，pbkdf2 加盐哈希） ----------

QQ_RE = re.compile(r"^\d{5,15}$")  # QQ 号：5-15 位纯数字


def hash_password(password):
    """生成 (salt_hex, hash_hex)，pbkdf2_hmac sha256 20 万次迭代"""
    salt = secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt), 200_000).hex()


def check_password(password, salt_hex, hash_hex):
    try:
        return hmac.compare_digest(
            hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                bytes.fromhex(salt_hex), 200_000).hex(),
            hash_hex)
    except (TypeError, ValueError):
        return False


def load_users():
    return load_json(USERS_FILE, {})


def save_users(users):
    save_json(USERS_FILE, users)


def find_user_by_qq(users, qq):
    return users.get(qq)


def user_login_limited(qq):
    """登录失败限流：5 分钟 5 次失败后锁定"""
    now_ts = time.time()
    fails = [x for x in LOGIN_FAILS.get(qq, []) if now_ts - x < LOGIN_LOCK_WINDOW]
    LOGIN_FAILS[qq] = fails
    if len(fails) >= LOGIN_MAX_FAILS:
        return True
    return False


def register_limited(fp):
    """同一设备 1 小时内最多注册 REGISTER_MAX 个账号"""
    now_ts = time.time()
    times = [x for x in REGISTER_TIMES.get(fp, []) if now_ts - x < REGISTER_WINDOW]
    REGISTER_TIMES[fp] = times
    return len(times) >= REGISTER_MAX


def claim_legacy_identity(fp, name, qq):
    """注册时从旧游客数据（visitors.json）继承身份。

    匹配规则（防止冒名顶替）：
    - 旧记录绑定了 QQ：QQ 必须与注册 QQ 一致（昵称+QQ 双重验证）
    - 旧记录没绑 QQ：当前设备指纹必须与旧记录一致
    继承内容：is_admin（管理员标记）、flags（违规计数）、旧 fp（保持历史点赞/发帖关联）。
    继承后删除对应旧游客记录，防止重复继承。
    返回 (旧游客记录或 None, 是否发生继承)
    """
    visitors = load_json(VISITORS_FILE, {})
    name_key = name.casefold()
    matched = [info for info in visitors.values()
               if str(info.get("name", "")).casefold() == name_key]
    if not matched:
        return None, False
    target = None
    qq_key = qq.casefold()
    for info in matched:
        old_qq = str(info.get("qq", "") or "")
        if old_qq:
            if old_qq.casefold() == qq_key:
                target = info
                break
        elif info.get("fp") == fp:
            target = info
    if target is None:
        return None, False
    visitors = {t: info for t, info in visitors.items()
                if str(info.get("name", "")).casefold() != name_key}
    save_json(VISITORS_FILE, visitors)
    return target, True


def load_posts():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_posts(posts):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_log(action, post_id, title, detail):
    """记录一条操作日志（最新在前，最多保留 MAX_LOG 条）"""
    logs = load_json(LOGS_FILE, [])
    logs.insert(0, {
        "time": now_str(),
        "action": action,
        "post_id": post_id,
        "title": title,
        "detail": detail,
    })
    save_json(LOGS_FILE, logs[:MAX_LOG])


def clean_text(s, key):
    if not isinstance(s, str):
        return ""
    return s.strip()[:MAX_LEN[key]]


def normalize_github(s):
    s = (s or "").strip().strip("/")
    s = re.sub(r"^https?://github\.com/", "", s)
    if s.endswith(".git"):
        s = s[:-4]
    s = s.strip()
    if not re.match(r"^[\w.-]+/[\w.-]+$", s):
        return ""
    return s


def github_info(repo):
    """从 GitHub API 拉取仓库描述，失败返回空串（不阻塞发布）"""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}",
            headers={"User-Agent": "hana-wall", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode("utf-8"))
            desc = info.get("description") or ""
            return desc.strip()[:MAX_LEN["content"]]
    except Exception:
        return ""


def make_post(data):
    ptype = data.get("type")
    if ptype not in ("need", "done"):
        return None, "类型错误：type 只能是 need 或 done"
    title = clean_text(data.get("title", ""), "title")
    if not title:
        return None, "标题不能为空"
    if len(title) < 2:
        return None, "标题至少 2 个字"
    content = clean_text(data.get("content", ""), "content")
    author = clean_text(data.get("author", ""), "author")
    contact = clean_text(data.get("contact", ""), "contact")
    github = ""
    if ptype == "done":
        repo = normalize_github(data.get("github", ""))
        if not repo:
            return None, "请填写 GitHub 仓库名（如：用户名/仓库名）"
        github = repo
        if not content:
            content = github_info(repo)
            if not content:
                return None, "描述不能为空（自动获取 GitHub 描述失败，请手动填写）"
    if ptype == "need" and not content:
        return None, "详细说明不能为空"
    reply_to = data.get("reply_to")
    try:
        reply_to = int(reply_to) if reply_to not in (None, "") else None
    except (TypeError, ValueError):
        reply_to = None
    return {
        "id": 0,
        "type": ptype,
        "title": title,
        "content": content,
        "author": author,
        "group": GROUP_NAME,
        "contact": contact,
        "github": github,
        "created_at": now_str(),
        "reply_to": reply_to,
        "claim": None,
    }, None


class Handler(BaseHTTPRequestHandler):
    server_version = "HanaWall/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if cache:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 20000:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        params = {}
        for pair in url.query.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                params[k] = v
        if path == "/api/posts":
            fp = params.get("fp", "")
            if not re.fullmatch(r"[0-9a-f]{8,64}", fp):
                fp = ""
            admin_view = check_token(params.get("token", ""))
            with LOCK:
                posts = load_posts()
                likes = load_json(LIKES_FILE, {})
                vmap = visitor_map()
            posts.sort(key=lambda p: p.get("id", 0), reverse=True)
            out = []
            for p in posts:
                if not admin_view and item_status(p) != "normal":
                    continue  # 待审/已屏蔽的帖子对访客整体隐藏，管理员全量可见
                arr = likes.get(str(p.get("id")), [])
                arr = [x if isinstance(x, dict) else {"fp": x, "name": ""} for x in arr]
                p["like_count"] = len(arr)
                p["liked"] = bool(fp and any(x["fp"] == fp for x in arr))
                names = []
                admins = []
                for x in arr:
                    fp2 = x.get("fp", "")
                    info = vmap.get(fp2) or {}
                    names.append(info.get("name") or x.get("name") or "匿名")
                    admins.append(bool(info.get("is_admin")))
                p["like_names"] = names
                p["like_admins"] = admins
                for c in p.get("comments", []):
                    fp2 = c.get("fp")
                    if fp2 and fp2 in vmap:
                        c["name"] = vmap[fp2]["name"]
                        c["is_admin"] = vmap[fp2]["is_admin"]
                    if not admin_view:
                        c.pop("sensitive", None)
                if not admin_view:
                    p.pop("sensitive", None)
                out.append(p)
            self._send(200, {"ok": True, "posts": out})
            return
        if path == "/api/wall":
            admin_view = check_token(params.get("token", ""))
            with LOCK:
                wall = load_json(WALL_FILE, [])
                vmap = visitor_map()
            wall.sort(key=lambda m: m.get("id", 0), reverse=True)
            for m in wall:
                fp2 = m.get("fp")
                if fp2 and fp2 in vmap:
                    m["name"] = vmap[fp2]["name"]
                    m["is_admin"] = vmap[fp2]["is_admin"]
                if not admin_view:
                    m.pop("sensitive", None)
            self._send(200, {"ok": True, "wall": wall})
            return
        if path == "/api/admin/logs":
            if not check_token(params.get("token", "")):
                self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                return
            logs = load_json(LOGS_FILE, [])
            self._send(200, {"ok": True, "logs": logs})
            return
        if path == "/api/admin/check":
            if not check_token(params.get("token", "")):
                self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                return
            self._send(200, {"ok": True})
            return
        if path == "/api/admin/pending":
            if not check_token(params.get("token", "")):
                self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                return
            with LOCK:
                posts = load_posts()
                wall = load_json(WALL_FILE, [])
                vmap = visitor_map()
            plist, clist, wlist = [], [], []
            for p in posts:
                if item_status(p) == "pending":
                    plist.append({
                        "kind": "post", "id": p.get("id"), "type": p.get("type"),
                        "title": p.get("title", ""), "content": p.get("content", ""),
                        "author": p.get("author") or "匿名", "sensitive": p.get("sensitive", []),
                        "fp": p.get("fp") or "", "created_at": p.get("created_at", ""),
                    })
                for c in p.get("comments", []):
                    if item_status(c) == "pending":
                        clist.append({
                            "kind": "comment", "pid": p.get("id"), "id": c.get("id"),
                            "title": p.get("title", ""), "content": c.get("content", ""),
                            "name": c.get("name") or "匿名", "sensitive": c.get("sensitive", []),
                            "fp": c.get("fp") or "", "created_at": c.get("created_at", ""),
                        })
            for m in wall:
                if item_status(m) == "pending":
                    wlist.append({
                        "kind": "wall", "id": m.get("id"), "content": m.get("content", ""),
                        "name": m.get("name") or "匿名", "sensitive": m.get("sensitive", []),
                        "fp": m.get("fp") or "", "created_at": m.get("created_at", ""),
                    })
            flags_by_fp = {}
            for info in load_json(USERS_FILE, {}).values():
                f = info.get("fp")
                if f and info.get("flags"):
                    flags_by_fp[f] = info["flags"]
            for it in plist + clist + wlist:
                it["flags"] = flags_by_fp.get(it.get("fp", ""), 0)
            self._send(200, {"ok": True, "posts": plist, "comments": clist, "wall": wlist})
            return
        if path == "/api/admin/trash":
            if not check_token(params.get("token", "")):
                self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                return
            trash = load_trash()
            for t in trash:
                t["kind_label"] = {"post": "帖子", "comment": "评论", "wall": "留言"}.get(t.get("kind"), "")
            self._send(200, {"ok": True, "trash": trash})
            return
        if path == "/api/announcement":
            ann = load_json(ANNOUNCEMENT_FILE, None)
            self._send(200, {"ok": True, "announcement": ann})
            return
        if path == "/api/user/me":
            info = get_visitor(params.get("token", ""))
            if not info:
                self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                return
            self._send(200, {"ok": True, "name": info.get("name"),
                             "qq": info.get("qq", ""), "is_admin": bool(info.get("is_admin"))})
            return
        if path in ("/", "/index.html", "/favicon.svg"):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            self._send(404, {"ok": False, "error": "Not Found"})
            return
        fpath = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            self._send(404, {"ok": False, "error": "Not Found"})
            return
        ctype = CONTENT_TYPES.get(os.path.splitext(fpath)[1], "application/octet-stream")
        # ETag 缓存：文件未变时浏览器直接 304，省带宽（部署后新文件 mtime 变化自动失效）
        try:
            st = os.stat(fpath)
            etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
        except OSError:
            etag = ""
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "max-age=0, must-revalidate")
            self.end_headers()
            return
        with open(fpath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if etag:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "max-age=0, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()
        if data is None:
            self._send(400, {"ok": False, "error": "请求格式错误"})
            return
        with LOCK:
            posts = load_posts()
            if path == "/api/admin/login":
                secret = load_secret()
                password = clean_text(data.get("password", ""), "password")
                if secret and hmac.compare_digest(password.encode("utf-8"), secret.encode("utf-8")):
                    token = secrets.token_hex(16)
                    TOKENS.add(token)
                    save_tokens()
                    self._send(200, {"ok": True, "token": token})
                else:
                    self._send(403, {"ok": False, "error": "口令错误"})
                return
            if path == "/api/user/register":
                qq = clean_text(data.get("qq", ""), "qq")
                if not QQ_RE.fullmatch(qq):
                    self._send(400, {"ok": False, "error": "QQ 号格式不正确（5-15 位数字）"})
                    return
                password = data.get("password", "")
                if not isinstance(password, str) or len(password) < 6 or len(password) > 20:
                    self._send(400, {"ok": False, "error": "密码长度需 6-20 位"})
                    return
                name = clean_text(data.get("name", ""), "name")
                if not name:
                    self._send(400, {"ok": False, "error": "请填写昵称"})
                    return
                fp = clean_text(data.get("fp", ""), "fp")
                if not re.fullmatch(r"[0-9a-f]{8,64}", fp):
                    self._send(400, {"ok": False, "error": "设备标识无效"})
                    return
                users = load_users()
                if qq in users:
                    self._send(409, {"ok": False, "error": "该 QQ 已注册，请直接登录"})
                    return
                if register_limited(fp):
                    self._send(429, {"ok": False, "error": "本设备注册过于频繁，请 1 小时后再试"})
                    return
                # 昵称继承旧游客身份（visitors.json）：昵称+QQ 或 昵称+设备 双重匹配才算本人
                legacy, inherited = claim_legacy_identity(fp, name, qq)
                token = secrets.token_hex(16)
                info = {
                    "token": token,
                    "name": name,
                    "fp": (legacy.get("fp") or fp) if inherited else fp,
                    "qq": qq,
                    "salt": "", "pw": "",
                    "is_admin": bool(legacy.get("is_admin")) if inherited else False,
                    "flags": legacy.get("flags", 0) if inherited else 0,
                    "created_at": now_str(),
                }
                if inherited:
                    info["qq"] = legacy.get("qq") or qq  # 旧记录绑定的 QQ 优先（可能与注册 QQ 不同，但已双重验证）
                info["salt"], info["pw"] = hash_password(password)
                users[qq] = info
                save_users(users)
                if inherited:
                    append_log("register", 0, name,
                               f"注册账号 {info['qq']}，继承旧游客身份（👑={'是' if info['is_admin'] else '否'}，违规 {info['flags']} 次）")
                else:
                    append_log("register", 0, name, f"注册账号 {info['qq']}（无旧游客数据可继承）")
                self._send(200, {"ok": True, "token": token, "name": info["name"],
                                 "qq": info["qq"], "is_admin": info["is_admin"],
                                 "inherited": inherited})
                return
            if path == "/api/user/login":
                qq = clean_text(data.get("qq", ""), "qq")
                password = data.get("password", "")
                if not isinstance(password, str) or len(password) > 20:
                    self._send(400, {"ok": False, "error": "请求格式错误"})
                    return
                if user_login_limited(qq):
                    self._send(429, {"ok": False, "error": "登录失败尝试过多，请 5 分钟后再试"})
                    return
                users = load_users()
                info = users.get(qq)
                if info is None:
                    LOGIN_FAILS.setdefault(qq, []).append(time.time())
                    self._send(404, {"ok": False, "error": "该 QQ 未注册，请先注册"})
                    return
                if not check_password(password, info.get("salt", ""), info.get("pw", "")):
                    LOGIN_FAILS.setdefault(qq, []).append(time.time())
                    self._send(403, {"ok": False, "error": "密码错误"})
                    return
                LOGIN_FAILS.pop(qq, None)
                fp = clean_text(data.get("fp", ""), "fp")
                if not re.fullmatch(r"[0-9a-f]{8,64}", fp):
                    self._send(400, {"ok": False, "error": "设备标识无效"})
                    return
                # 设备迁移：登录后当前设备成为该账号的主设备（点赞/发帖跟随）
                if info.get("fp") != fp:
                    append_log("login", 0, info.get("name", qq),
                               f"登录：设备迁移 {str(info.get('fp', ''))[:8]}… → {fp[:8]}…")
                    info["fp"] = fp
                if not info.get("token"):
                    info["token"] = secrets.token_hex(16)  # 该账号首个登录凭证
                save_users(users)
                self._send(200, {"ok": True, "token": info["token"], "name": info.get("name", qq),
                                 "qq": info["qq"], "is_admin": bool(info.get("is_admin"))})
                return
            if path == "/api/user/logout":
                users = load_users()
                token = clean_text(data.get("token", ""), "token")
                for info in users.values():
                    if info.get("token") == token:
                        info["token"] = ""  # 凭证作废，其他设备同时下线（与旧游客体系行为一致）
                        save_users(users)
                        break
                self._send(200, {"ok": True})
                return
            if path == "/api/user/update":
                """修改昵称（仅限本人；登录态下随时可改）"""
                info = get_visitor(clean_text(data.get("token", ""), "token"))
                if not info:
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                name = clean_text(data.get("name", ""), "name")
                if not name:
                    self._send(400, {"ok": False, "error": "昵称不能为空"})
                    return
                if info.get("name") != name:
                    users = load_users()
                    users[info["qq"]]["name"] = name
                    save_users(users)
                    append_log("rename", 0, name, f"修改昵称（账号 {info['qq']}）")
                self._send(200, {"ok": True, "name": name})
                return
            if path == "/api/admin/user/reset_password":
                """管理员重置指定 QQ 账号的密码（李的授权工具，找回密码走人工通道）"""
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                qq = clean_text(data.get("qq", ""), "qq")
                password = data.get("password", "")
                if not QQ_RE.fullmatch(qq):
                    self._send(400, {"ok": False, "error": "QQ 号格式不正确"})
                    return
                if not isinstance(password, str) or len(password) < 6 or len(password) > 20:
                    self._send(400, {"ok": False, "error": "密码长度需 6-20 位"})
                    return
                users = load_users()
                info = users.get(qq)
                if info is None:
                    self._send(404, {"ok": False, "error": "该 QQ 未注册"})
                    return
                info["salt"], info["pw"] = hash_password(password)
                info["token"] = ""  # 重置后强制重新登录
                save_users(users)
                append_log("reset_pw", 0, info.get("name", qq), f"管理员重置密码（账号 {qq}）")
                self._send(200, {"ok": True})
                return
            if path == "/api/admin/user/set_admin":
                """管理员授权/撤销某账号的管理员标记（想当管理员需向李申请）"""
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                qq = clean_text(data.get("qq", ""), "qq")
                if not QQ_RE.fullmatch(qq):
                    self._send(400, {"ok": False, "error": "QQ 号格式不正确"})
                    return
                users = load_users()
                info = users.get(qq)
                if info is None:
                    self._send(404, {"ok": False, "error": "该 QQ 未注册"})
                    return
                is_admin = bool(data.get("is_admin"))
                info["is_admin"] = is_admin
                save_users(users)
                append_log("set_admin", 0, info.get("name", qq),
                           f"{'授予' if is_admin else '撤销'}管理员（账号 {qq}）")
                self._send(200, {"ok": True, "is_admin": is_admin})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/edit$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                title = clean_text(data.get("title", ""), "title")
                content = clean_text(data.get("content", ""), "content")
                author = clean_text(data.get("author", ""), "author")
                contact = clean_text(data.get("contact", ""), "contact")
                if not title:
                    self._send(400, {"ok": False, "error": "标题不能为空"})
                    return
                if post["type"] == "need" and not content:
                    self._send(400, {"ok": False, "error": "详细说明不能为空"})
                    return
                if post["type"] == "done":
                    repo = normalize_github(data.get("github", ""))
                    if not repo:
                        self._send(400, {"ok": False, "error": "请填写 GitHub 仓库名（如：用户名/仓库名）"})
                        return
                    post["github"] = repo
                    if not content:
                        fetched = github_info(repo)
                        if not fetched:
                            self._send(400, {"ok": False, "error": "描述不能为空（自动获取 GitHub 描述失败，请手动填写）"})
                            return
                        content = fetched
                    try:
                        reply_to = int(data.get("reply_to")) if data.get("reply_to") not in (None, "") else None
                    except (TypeError, ValueError):
                        reply_to = post.get("reply_to")
                    post["reply_to"] = reply_to
                old = {k: post.get(k, "") for k in ("title", "content", "author", "contact", "github")}
                post["title"] = title
                post["content"] = content
                if author:
                    post["author"] = author
                post["contact"] = contact
                changes = []
                labels = {"title": "标题", "content": "内容", "author": "昵称", "contact": "联系方式", "github": "仓库"}
                for k, label in labels.items():
                    if old[k] != post.get(k, ""):
                        if k == "content":
                            changes.append("内容已修改")
                        elif k == "title":
                            changes.append(f"标题「{old[k]}」→「{post[k]}」")
                        else:
                            changes.append(f"{label}「{old[k]}」→「{post[k]}」")
                append_log("edit", pid, post["title"], "；".join(changes) or "无字段变化")
                words = find_sensitive(post["title"] + "\n" + post.get("content", ""))
                if words:
                    post["status"] = "pending"
                    post["sensitive"] = words
                else:
                    post["status"] = "normal"
                    post.pop("sensitive", None)
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/delete$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                move_to_trash("post", 0, post, post.get("title", ""), post.get("author") or "匿名")
                posts.remove(post)
                if post["type"] == "need":
                    for p in posts:
                        if p.get("reply_to") == pid:
                            p["reply_to"] = None
                who = post.get("author") or "匿名"
                content = post.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("trash", pid, post.get("title", ""),
                           f"删除卡片入回收站（作者：{who}）内容：{content}")
                save_posts(posts)
                self._send(200, {"ok": True})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/comments/(\d+)/delete$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                cid = int(m.group(2))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                comments = post.get("comments", [])
                comment = next((c for c in comments if c.get("id") == cid), None)
                if comment is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                move_to_trash("comment", pid, comment, post.get("title", ""), comment.get("name") or "匿名")
                comments.remove(comment)
                for c in comments:
                    if c.get("reply_to") == cid:
                        c["reply_to"] = None  # 子回复变顶级，避免悬空
                who = comment.get("name") or "匿名"
                content = comment.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("trash", pid, post.get("title", ""),
                           f"删除评论入回收站（作者：{who}）内容：{content}")
                save_posts(posts)
                self._send(200, {"ok": True})
                return
            m = re.match(r"^/api/admin/wall/(\d+)/delete$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                mid = int(m.group(1))
                wall = load_json(WALL_FILE, [])
                msg = next((m0 for m0 in wall if m0.get("id") == mid), None)
                if msg is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                move_to_trash("wall", 0, msg, "留言板", msg.get("name") or "匿名")
                wall.remove(msg)
                for m0 in wall:
                    if m0.get("reply_to") == mid:
                        m0["reply_to"] = None
                who = msg.get("name") or "匿名"
                content = msg.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("trash", 0, "留言板",
                           f"删除留言入回收站（作者：{who}）内容：{content}")
                save_json(WALL_FILE, wall)
                self._send(200, {"ok": True})
                return
            m = re.match(r"^/api/admin/trash/(\d+)/restore$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                tid = int(m.group(1))
                trash = load_trash()
                t = next((x for x in trash if x.get("tid") == tid), None)
                if t is None:
                    self._send(404, {"ok": False, "error": "回收站条目不存在"})
                    return
                kind, data = t.get("kind"), t.get("data")
                if kind == "post":
                    if any(p.get("id") == data.get("id") for p in posts):
                        self._send(409, {"ok": False, "error": "该帖子已存在，无法恢复"})
                        return
                    posts.append(data)
                    save_posts(posts)
                elif kind == "comment":
                    post = next((p for p in posts if p["id"] == t.get("pid")), None)
                    if post is None:
                        self._send(404, {"ok": False, "error": "原帖子已不存在，无法恢复该评论"})
                        return
                    comments = post.setdefault("comments", [])
                    if any(c.get("id") == data.get("id") for c in comments):
                        self._send(409, {"ok": False, "error": "该评论已存在，无法恢复"})
                        return
                    comments.append(data)
                    save_posts(posts)
                elif kind == "wall":
                    wall = load_json(WALL_FILE, [])
                    if any(m0.get("id") == data.get("id") for m0 in wall):
                        self._send(409, {"ok": False, "error": "该留言已存在，无法恢复"})
                        return
                    wall.append(data)
                    save_json(WALL_FILE, wall)
                else:
                    self._send(400, {"ok": False, "error": "未知类型"})
                    return
                trash.remove(t)
                save_trash(trash)
                append_log("restore", tid, t.get("title", ""),
                           f"恢复{ {'post': '帖子', 'comment': '评论', 'wall': '留言'}.get(kind, kind) }（{t.get('who', '')}）")
                self._send(200, {"ok": True})
                return
            m = re.match(r"^/api/admin/trash/(\d+)/purge$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                tid = int(m.group(1))
                trash = load_trash()
                t = next((x for x in trash if x.get("tid") == tid), None)
                if t is None:
                    self._send(404, {"ok": False, "error": "回收站条目不存在"})
                    return
                trash.remove(t)
                save_trash(trash)
                append_log("purge", tid, t.get("title", ""),
                           f"彻底删除回收站条目（{t.get('who', '')}）")
                self._send(200, {"ok": True})
                return
            if path == "/api/admin/trash/clear":
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                save_trash([])
                append_log("clear", 0, "回收站", "清空回收站（全部彻底删除）")
                self._send(200, {"ok": True})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/sink$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                sunk = bool(data.get("sunk"))
                post["sunk"] = sunk
                append_log("sink" if sunk else "unsink", pid, post.get("title", ""),
                           "沉底" if sunk else "恢复显示")
                save_posts(posts)
                self._send(200, {"ok": True, "sunk": sunk})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/comments/(\d+)/hide$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                cid = int(m.group(2))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                comment = next((c for c in post.get("comments", []) if c.get("id") == cid), None)
                if comment is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                hidden = bool(data.get("hidden"))
                set_status(comment, "hidden" if hidden else "normal")
                if not hidden:
                    comment.pop("sensitive", None)
                who = comment.get("name") or "匿名"
                content = comment.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("hide" if hidden else "unhide", pid, post.get("title", ""),
                           (f"屏蔽留言（作者：{who}）内容：{content}" if hidden
                            else f"解除屏蔽留言（作者：{who}）内容：{content}"))
                save_posts(posts)
                self._send(200, {"ok": True, "hidden": hidden})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/comments/(\d+)/review$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                cid = int(m.group(2))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                comment = next((c for c in post.get("comments", []) if c.get("id") == cid), None)
                if comment is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                status = data.get("status")
                if status not in ("normal", "hidden"):
                    self._send(400, {"ok": False, "error": "status 只能是 normal 或 hidden"})
                    return
                set_status(comment, status)
                if status == "normal":
                    comment.pop("sensitive", None)
                who = comment.get("name") or "匿名"
                words = "、".join(comment.get("sensitive", [])) or "—"
                append_log("review", pid, post.get("title", ""),
                           f"审核帖子留言（作者：{who}，命中词：{words}）：{'放行' if status == 'normal' else '屏蔽'}")
                save_posts(posts)
                self._send(200, {"ok": True, "status": status})
                return
            m = re.match(r"^/api/admin/wall/(\d+)/hide$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                mid = int(m.group(1))
                wall = load_json(WALL_FILE, [])
                msg = next((m0 for m0 in wall if m0.get("id") == mid), None)
                if msg is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                hidden = bool(data.get("hidden"))
                set_status(msg, "hidden" if hidden else "normal")
                if not hidden:
                    msg.pop("sensitive", None)
                who = msg.get("name") or "匿名"
                content = msg.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("hide" if hidden else "unhide", 0, "留言板",
                           (f"屏蔽留言（作者：{who}）内容：{content}" if hidden
                            else f"解除屏蔽留言（作者：{who}）内容：{content}"))
                save_json(WALL_FILE, wall)
                self._send(200, {"ok": True, "hidden": hidden})
                return
            m = re.match(r"^/api/admin/wall/(\d+)/review$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                mid = int(m.group(1))
                wall = load_json(WALL_FILE, [])
                msg = next((m0 for m0 in wall if m0.get("id") == mid), None)
                if msg is None:
                    self._send(404, {"ok": False, "error": "留言不存在"})
                    return
                status = data.get("status")
                if status not in ("normal", "hidden"):
                    self._send(400, {"ok": False, "error": "status 只能是 normal 或 hidden"})
                    return
                set_status(msg, status)
                if status == "normal":
                    msg.pop("sensitive", None)
                who = msg.get("name") or "匿名"
                words = "、".join(msg.get("sensitive", [])) or "—"
                append_log("review", 0, "留言板",
                           f"审核留言板留言（作者：{who}，命中词：{words}）：{'放行' if status == 'normal' else '屏蔽'}")
                save_json(WALL_FILE, wall)
                self._send(200, {"ok": True, "status": status})
                return
            m = re.match(r"^/api/admin/posts/(\d+)/review$", path)
            if m:
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                status = data.get("status")
                if status not in ("normal", "hidden"):
                    self._send(400, {"ok": False, "error": "status 只能是 normal 或 hidden"})
                    return
                set_status(post, status)
                if status == "normal":
                    post.pop("sensitive", None)
                who = post.get("author") or "匿名"
                words = "、".join(post.get("sensitive", [])) or "—"
                append_log("review", pid, post.get("title", ""),
                           f"审核帖子（作者：{who}，命中词：{words}）：{'放行' if status == 'normal' else '屏蔽'}")
                save_posts(posts)
                self._send(200, {"ok": True, "status": status})
                return
            if path == "/api/admin/announcement":
                if not check_token(clean_text(data.get("token", ""), "token")):
                    self._send(401, {"ok": False, "error": "未登录或登录已过期"})
                    return
                content = clean_text(data.get("content", ""), "announcement")
                if content:
                    ann = {"content": content, "updated_at": now_str()}
                    save_json(ANNOUNCEMENT_FILE, ann)
                else:
                    ann = None
                    if os.path.exists(ANNOUNCEMENT_FILE):
                        os.remove(ANNOUNCEMENT_FILE)
                self._send(200, {"ok": True, "announcement": ann})
                return
            m = re.match(r"^/api/posts/(\d+)/claim$", path)
            if m:
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                if post["type"] != "need":
                    self._send(400, {"ok": False, "error": "只有需求可以认领"})
                    return
                visitor = get_visitor(clean_text(data.get("token", ""), "token"))
                if not visitor:
                    self._send(401, {"ok": False, "error": "请先登录"})
                    return
                fp = visitor.get("fp", "")
                claim = post.get("claim")
                if claim:
                    if claim.get("fp") != fp:
                        self._send(403, {"ok": False, "error": f"该需求已被 {claim.get('name', '')} 认领"})
                        return
                    post["claim"] = None
                    save_posts(posts)
                    self._send(200, {"ok": True, "claim": None})
                else:
                    post["claim"] = {"name": visitor["name"], "fp": fp, "time": now_str()}
                    save_posts(posts)
                    self._send(200, {"ok": True, "claim": post["claim"]})
                return
            if path == "/api/posts":
                visitor = get_visitor(clean_text(data.get("token", ""), "token"))
                if not visitor:
                    self._send(401, {"ok": False, "error": "请先登录"})
                    return
                post, err = make_post(data)
                if err:
                    self._send(400, {"ok": False, "error": err})
                    return
                post["author"] = visitor["name"]
                post["fp"] = visitor.get("fp", "")
                post["id"] = (posts[-1]["id"] + 1) if posts else 1
                words = find_sensitive(post["title"] + "\n" + post["content"])
                if words:
                    post["status"] = "pending"
                    post["sensitive"] = words
                    uinfo = get_visitor(clean_text(data.get("token", ""), "token"))
                    if uinfo and uinfo.get("qq"):
                        users = load_users()
                        u = users.get(uinfo["qq"])
                        if u:
                            u["flags"] = u.get("flags", 0) + 1
                            save_users(users)
                posts.append(post)
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
            m = re.match(r"^/api/posts/(\d+)/like$", path)
            if m:
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                fp = clean_text(data.get("fp", ""), "fp")
                if not re.fullmatch(r"[0-9a-f]{8,64}", fp):
                    self._send(400, {"ok": False, "error": "设备标识无效"})
                    return
                name = check_visitor(clean_text(data.get("token", ""), "token"))
                if not name:
                    self._send(401, {"ok": False, "error": "请先登录"})
                    return
                likes = load_json(LIKES_FILE, {})
                key = str(pid)
                arr = [x if isinstance(x, dict) else {"fp": x, "name": ""} for x in likes.setdefault(key, [])]
                idx = next((i for i, x in enumerate(arr) if x["fp"] == fp), None)
                if idx is not None:
                    arr.pop(idx)
                    liked = False
                else:
                    arr.append({"fp": fp, "name": name})
                    liked = True
                if not arr:
                    del likes[key]
                else:
                    likes[key] = arr
                save_json(LIKES_FILE, likes)
                vmap = visitor_map()
                names = []
                admins = []
                for x in arr:
                    fp2 = x.get("fp", "")
                    info = vmap.get(fp2) or {}
                    names.append(info.get("name") or x.get("name") or "匿名")
                    admins.append(bool(info.get("is_admin")))
                self._send(200, {"ok": True, "liked": liked, "count": len(arr),
                                 "like_names": names, "like_admins": admins})
                return
            if path == "/api/wall":
                visitor = get_visitor(clean_text(data.get("token", ""), "token"))
                if not visitor:
                    self._send(401, {"ok": False, "error": "请先登录"})
                    return
                name = visitor["name"]
                fp = visitor.get("fp", "")
                content = clean_text(data.get("content", ""), "wall_content")
                if not content:
                    self._send(400, {"ok": False, "error": "留言内容不能为空"})
                    return
                try:
                    reply_to = int(data.get("reply_to")) if data.get("reply_to") not in (None, "") else None
                except (TypeError, ValueError):
                    reply_to = None
                wall = load_json(WALL_FILE, [])
                if reply_to is not None and not any(m.get("id") == reply_to for m in wall):
                    self._send(400, {"ok": False, "error": "回复的留言不存在"})
                    return
                mid = max((m.get("id", 0) for m in wall), default=0) + 1
                message = {
                    "id": mid,
                    "name": name,
                    "fp": fp,
                    "content": content,
                    "reply_to": reply_to,
                    "created_at": now_str(),
                }
                words = find_sensitive(content)
                if words:
                    message["status"] = "pending"
                    message["sensitive"] = words
                    uinfo = get_visitor(clean_text(data.get("token", ""), "token"))
                    if uinfo and uinfo.get("qq"):
                        users = load_users()
                        u = users.get(uinfo["qq"])
                        if u:
                            u["flags"] = u.get("flags", 0) + 1
                            save_users(users)
                wall.append(message)
                save_json(WALL_FILE, wall)
                self._send(200, {"ok": True, "message": message})
                return
            m = re.match(r"^/api/posts/(\d+)/comments$", path)
            if m:
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                visitor = get_visitor(clean_text(data.get("token", ""), "token"))
                if not visitor:
                    self._send(401, {"ok": False, "error": "请先登录"})
                    return
                name = visitor["name"]
                fp = visitor.get("fp", "")
                content = clean_text(data.get("content", ""), "comment")
                if not content:
                    self._send(400, {"ok": False, "error": "留言内容不能为空"})
                    return
                try:
                    reply_to = int(data.get("reply_to")) if data.get("reply_to") not in (None, "") else None
                except (TypeError, ValueError):
                    reply_to = None
                comments = post.setdefault("comments", [])
                if reply_to is not None and not any(c.get("id") == reply_to for c in comments):
                    self._send(400, {"ok": False, "error": "回复的留言不存在"})
                    return
                cid = max((c.get("id", 0) for c in comments), default=0) + 1
                comment = {
                    "id": cid,
                    "name": name,
                    "fp": fp,
                    "content": content,
                    "reply_to": reply_to,
                    "created_at": now_str(),
                }
                words = find_sensitive(content)
                if words:
                    comment["status"] = "pending"
                    comment["sensitive"] = words
                    uinfo = get_visitor(clean_text(data.get("token", ""), "token"))
                    if uinfo and uinfo.get("qq"):
                        users = load_users()
                        u = users.get(uinfo["qq"])
                        if u:
                            u["flags"] = u.get("flags", 0) + 1
                            save_users(users)
                comments.append(comment)
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
        self._send(404, {"ok": False, "error": "Not Found"})


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    load_tokens()
    n = load_sensitive()
    print(f"敏感词库已加载：{SENSITIVE_FILE}（{n} 条有效词）")
    port = int(os.environ.get("PORT", "3000"))
    print(f"Hana 插件需求墙已启动：http://0.0.0.0:{port}（数据文件：{DATA_FILE}）")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
