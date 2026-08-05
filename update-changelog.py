#!/usr/bin/env python3
"""更新日志自动追加：基于 git 提交记录，把上次记录之后的提交按日期合并追加。

- 每个日期 = 一个分组（同日多次部署合并），最新日期在最上
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
        logs = git("log", "--format=%H|%ad|%s", "--date=short", f"{last}..HEAD", "--reverse")
    else:
        logs = git("log", "--format=%H|%ad|%s", "--date=short", "--reverse")

    entries = []  # (date, title)
    for line in logs.splitlines():
        h, _, rest = line.partition("|")
        date, _, msg = rest.partition("|")
        files = set(git("show", "--format=", "--name-only", h).split())
        if not files or not (files & USER_VISIBLE):
            continue
        entries.append((date, msg.strip() or h[:8]))

    if not entries:
        print("更新日志：无新提交，无需追加")
        return

    groups = data.setdefault("groups", [])
    by_date = {g.get("date"): g for g in groups}
    # 新提交按日期分组（同一天合并，去重，新条目在前）
    new_groups = {}
    for date, msg in entries:
        new_groups.setdefault(date, [])
        if msg not in new_groups[date]:
            new_groups[date].insert(0, msg)
    for date in sorted(new_groups, reverse=True):
        if date in by_date:
            # 合并进已有分组：新条目插到最前，去重
            existing = by_date[date]
            merged = [m for m in new_groups[date] if m not in existing["items"]]
            existing["items"] = merged + existing["items"]
        else:
            groups.insert(0, {"date": date, "items": new_groups[date]})

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["updated_at"] = now
    data["last_commit"] = git("rev-parse", "HEAD")
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"更新日志：已按日期追加（{now}）")
    for date in sorted(new_groups, reverse=True):
        for e in new_groups[date]:
            print(f"  {date} - {e}")


if __name__ == "__main__":
    main()
    sys.exit(0)
