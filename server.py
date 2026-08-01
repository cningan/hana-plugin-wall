#!/usr/bin/env python3
"""Hana 插件需求墙 - 单文件服务（仅用 Python 标准库，无第三方依赖）

- 静态页面：static/ 目录
- 数据存储：data.json / logs.json / likes.json / wall.json（默认同目录，可用 HANA_WALL_DATA_DIR 指定目录）
- 端口：默认 3000，可用环境变量 PORT 覆盖
"""

import hmac
import json
import os
import re
import secrets
import threading
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
STATIC_DIR = os.path.join(BASE_DIR, "static")
SECRET_FILE = os.path.join(BASE_DIR, "secret.txt")

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
}

MAX_LOG = 500  # 操作日志最多保留条数
MAX_VISITORS = 1000  # 游客账号最多保留数（超出删最早）

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


def check_token(token):
    return bool(token) and token in TOKENS


def load_tokens():
    """启动时从文件加载管理令牌（重启不失效）"""
    global TOKENS
    TOKENS = set(load_json(ADMINS_FILE, []))


def save_tokens():
    save_json(ADMINS_FILE, sorted(TOKENS))


def check_visitor(token):
    """校验游客令牌，有效返回昵称，无效返回 None"""
    if not token:
        return None
    visitors = load_json(VISITORS_FILE, {})
    info = visitors.get(token)
    return info.get("name") if info else None


def get_visitor(token):
    """校验游客令牌，有效返回记录 dict（含 fp/name），无效返回 None"""
    if not token:
        return None
    visitors = load_json(VISITORS_FILE, {})
    return visitors.get(token) or None


def visitor_map():
    """设备指纹 -> {name, is_admin}（用于显示时归一化 + 管理员标签）"""
    visitors = load_json(VISITORS_FILE, {})
    m = {}
    for info in visitors.values():
        if info.get("fp"):
            m[info["fp"]] = {
                "name": info.get("name", ""),
                "is_admin": bool(info.get("is_admin")),
            }
    return m


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
            with LOCK:
                posts = load_posts()
                likes = load_json(LIKES_FILE, {})
                vmap = visitor_map()
            posts.sort(key=lambda p: p.get("id", 0), reverse=True)
            for p in posts:
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
            self._send(200, {"ok": True, "posts": posts})
            return
        if path == "/api/wall":
            with LOCK:
                wall = load_json(WALL_FILE, [])
                vmap = visitor_map()
            wall.sort(key=lambda m: m.get("id", 0), reverse=True)
            for m in wall:
                fp2 = m.get("fp")
                if fp2 and fp2 in vmap:
                    m["name"] = vmap[fp2]["name"]
                    m["is_admin"] = vmap[fp2]["is_admin"]
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
        if path == "/api/announcement":
            ann = load_json(ANNOUNCEMENT_FILE, None)
            self._send(200, {"ok": True, "announcement": ann})
            return
        if path == "/api/visitor/me":
            name = check_visitor(params.get("token", ""))
            if not name:
                self._send(401, {"ok": False, "error": "未登录"})
                return
            self._send(200, {"ok": True, "name": name})
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
        with open(fpath, "rb") as f:
            self._send(200, f.read(), ctype, cache=True)

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
                    fp = clean_text(data.get("fp", ""), "fp")
                    if re.fullmatch(r"[0-9a-f]{8,64}", fp):
                        visitors = load_json(VISITORS_FILE, {})
                        changed = False
                        for t, info in visitors.items():
                            if info.get("fp") == fp:
                                info["is_admin"] = True
                                changed = True
                        if changed:
                            save_json(VISITORS_FILE, visitors)
                    self._send(200, {"ok": True, "token": token})
                else:
                    self._send(403, {"ok": False, "error": "口令错误"})
                return
            if path == "/api/visitor/login":
                name = clean_text(data.get("name", ""), "name")
                if not name:
                    self._send(400, {"ok": False, "error": "请填写昵称"})
                    return
                fp = clean_text(data.get("fp", ""), "fp")
                if not re.fullmatch(r"[0-9a-f]{8,64}", fp):
                    self._send(400, {"ok": False, "error": "设备标识无效"})
                    return
                visitors = load_json(VISITORS_FILE, {})
                for t in [t for t, info in visitors.items() if info.get("fp") == fp]:
                    del visitors[t]
                name_key = name.casefold()
                for info in visitors.values():
                    if info.get("fp") != fp and str(info.get("name", "")).casefold() == name_key:
                        self._send(409, {"ok": False, "error": "昵称已被占用，换一个吧"})
                        return
                token = secrets.token_hex(16)
                visitors[token] = {"fp": fp, "name": name, "created_at": now_str()}
                if len(visitors) > MAX_VISITORS:
                    for t in sorted(visitors, key=lambda t: visitors[t].get("created_at", ""))[:len(visitors) - MAX_VISITORS]:
                        del visitors[t]
                save_json(VISITORS_FILE, visitors)
                self._send(200, {"ok": True, "token": token, "name": name})
                return
            if path == "/api/visitor/logout":
                visitors = load_json(VISITORS_FILE, {})
                token = clean_text(data.get("token", ""), "token")
                if token in visitors:
                    del visitors[token]
                    save_json(VISITORS_FILE, visitors)
                self._send(200, {"ok": True})
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
                posts.remove(post)
                if post["type"] == "need":
                    for p in posts:
                        if p.get("reply_to") == pid:
                            p["reply_to"] = None
                who = post.get("author") or "匿名"
                content = post.get("content", "")
                if len(content) > 60:
                    content = content[:60] + "……"
                append_log("delete", pid, post.get("title", ""),
                           f"删除卡片（作者：{who}）内容：{content}")
                save_posts(posts)
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
                    self._send(401, {"ok": False, "error": "请先设置昵称再认领"})
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
                    self._send(401, {"ok": False, "error": "请先设置昵称再发布"})
                    return
                post, err = make_post(data)
                if err:
                    self._send(400, {"ok": False, "error": err})
                    return
                post["author"] = visitor["name"]
                post["id"] = (posts[-1]["id"] + 1) if posts else 1
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
                    self._send(401, {"ok": False, "error": "请先设置昵称再点赞"})
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
                    self._send(401, {"ok": False, "error": "请先设置昵称再留言"})
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
                wall.append({
                    "id": mid,
                    "name": name,
                    "fp": fp,
                    "content": content,
                    "reply_to": reply_to,
                    "created_at": now_str(),
                })
                save_json(WALL_FILE, wall)
                self._send(200, {"ok": True, "message": wall[-1]})
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
                    self._send(401, {"ok": False, "error": "请先设置昵称再留言"})
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
                comments.append({
                    "id": cid,
                    "name": name,
                    "fp": fp,
                    "content": content,
                    "reply_to": reply_to,
                    "created_at": now_str(),
                })
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
        self._send(404, {"ok": False, "error": "Not Found"})


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    load_tokens()
    port = int(os.environ.get("PORT", "3000"))
    print(f"Hana 插件需求墙已启动：http://0.0.0.0:{port}（数据文件：{DATA_FILE}）")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
