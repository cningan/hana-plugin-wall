#!/usr/bin/env python3
"""Hana 插件需求墙 - 单文件服务（仅用 Python 标准库，无第三方依赖）

- 静态页面：static/ 目录
- 数据存储：data.json（同目录，自动创建）
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
DATA_FILE = os.path.join(BASE_DIR, "data.json")
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
}

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


def clean_text(s, key):
    if not isinstance(s, str):
        return ""
    return s.strip()[:MAX_LEN[key]]


def normalize_github(s):
    s = (s or "").strip().strip("/")
    s = re.sub(r"^https?://github\.com/", "", s)
    s = s.rstrip(".git").strip()
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

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
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
        path = urlparse(self.path).path
        if path == "/api/posts":
            with LOCK:
                posts = load_posts()
            posts.sort(key=lambda p: p.get("id", 0), reverse=True)
            self._send(200, {"ok": True, "posts": posts})
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
            self._send(200, f.read(), ctype)

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
                    self._send(200, {"ok": True, "token": token})
                else:
                    self._send(403, {"ok": False, "error": "口令错误"})
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
                post["title"] = title
                post["content"] = content
                if author:
                    post["author"] = author
                post["contact"] = contact
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
                save_posts(posts)
                self._send(200, {"ok": True})
                return
            if path == "/api/posts":
                post, err = make_post(data)
                if err:
                    self._send(400, {"ok": False, "error": err})
                    return
                post["id"] = (posts[-1]["id"] + 1) if posts else 1
                posts.append(post)
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
            m = re.match(r"^/api/posts/(\d+)/comments$", path)
            if m:
                pid = int(m.group(1))
                post = next((p for p in posts if p["id"] == pid), None)
                if post is None:
                    self._send(404, {"ok": False, "error": "帖子不存在"})
                    return
                name = clean_text(data.get("name", ""), "name") or "匿名"
                content = clean_text(data.get("content", ""), "comment")
                if not content:
                    self._send(400, {"ok": False, "error": "留言内容不能为空"})
                    return
                post.setdefault("comments", []).append({
                    "name": name,
                    "content": content,
                    "created_at": now_str(),
                })
                save_posts(posts)
                self._send(200, {"ok": True, "post": post})
                return
        self._send(404, {"ok": False, "error": "Not Found"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    print(f"Hana 插件需求墙已启动：http://0.0.0.0:{port}（数据文件：{DATA_FILE}）")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
