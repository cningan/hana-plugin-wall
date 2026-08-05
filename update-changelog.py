#!/usr/bin/env python3
"""更新日志自动追加：基于 git 提交记录，把上次记录之后的提交追加为新的版本条目。

- 每个新版本 = 一次部署前的未记录提交集合（items = 提交标题）
- 只改 changelog.json 的提交会被跳过（避免自引用）
- 用法：python update-changelog.py（由 deploy.ps1 在部署前自动调用）
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(REPO, "static", "changelog.json")

# 只有修改用户可见代码的提交才进更新日志（脚本/部署工具/changelog 自身改动跳过）
USER_VISIBLE = {"server.py", "static/app.js", "static/index.html", "static/style.css"}


def git(*args):
    r = subprocess.run(["git", "-C", REPO, *args],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台 GBK 兜底，防 emoji 打印崩溃
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(CFG, encoding="utf-8-sig") as f:
        data = json.load(f)
    last = data.get("last_commit", "")

    if last:
        logs = git("log", "--format=%H|%s", f"{last}..HEAD", "--reverse")
    else:
        logs = git("log", "--format=%H|%s", "--reverse")

    entries = []
    for line in logs.splitlines():
        h, _, msg = line.partition("|")
        files = set(git("show", "--format=", "--name-only", h).split())
        if not files or not (files & USER_VISIBLE):
            continue
        entries.append(msg.strip() or h[:8])

    if not entries:
        print("更新日志：无新提交，无需追加")
        return

    versions = [int(m.group(1)) for v in data["versions"]
                if (m := re.match(r"v(\d+)$", v.get("version", "")))]
    newv = (max(versions) + 1) if versions else 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["versions"].insert(0, {
        "version": f"v{newv}",
        "date": now,
        "items": entries,
    })
    data["updated_at"] = now
    data["last_commit"] = git("rev-parse", "HEAD")
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"更新日志：已自动追加 v{newv}（{now}）")
    for e in entries:
        print("  - " + e)


if __name__ == "__main__":
    main()
    sys.exit(0)
