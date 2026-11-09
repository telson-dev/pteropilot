#!/usr/bin/env python3
"""用 CHANGELOG.md 的内容补全 GitHub Release 的说明（release notes）。

为什么需要：`release.yml` 里是 `generate_release_notes: true`，它只会写一行
"**Full Changelog**: <链接>"——用户打开发布页看不到这个版本到底改了什么。
这个脚本把 CHANGELOG 里对应版本的章节搬进 Release 描述，并附上安装指引。

用法（PowerShell）：
    $env:GH_TOKEN = "<token>"
    python scripts/sync_release_notes.py                 # 同步最新 tag
    python scripts/sync_release_notes.py --tag v0.2.22   # 指定 tag
    python scripts/sync_release_notes.py --dry-run       # 只看会改成什么
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "telson-dev/pteropilot"
CHANGELOG = ROOT / "CHANGELOG.md"

# CHANGELOG 里的英文小标题 → Release 里更易读的中文小标题
SECTION_LABELS = {
    "Added": "新增",
    "Changed": "变更",
    "Fixed": "修复",
    "Removed": "移除",
    "Security": "安全",
    "Deprecated": "弃用",
    "Performance": "性能",
    "Docs": "文档",
}

INSTALL_SECTION = """## 安装

```bash
# 从本 Release 的 wheel（推荐，PyPI 可能滞后）
pip install pteropilot-{version}-py3-none-any.whl

# 或从包索引（若已同步）
uv tool install pteropilot
```

装好可自检版本与工具数：

```bash
pteropilot --version
```
"""


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method)
    req.add_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pteropilot-release-notes")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, {"error": raw[:300]}


def changelog_section(version: str) -> str | None:
    """取出 CHANGELOG 里 `## [<version>]` 这一节的正文（不含标题行）。"""
    text = CHANGELOG.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = re.match(r"^## +\[" + re.escape(version) + r"\]", line)
        if m:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^## +\[", lines[j]) or lines[j].startswith("## "):
            end = j
            break
    body = "\n".join(lines[start:end]).strip()
    return body or None


def translate_sections(body: str) -> str:
    out = []
    for line in body.splitlines():
        m = re.match(r"^### +(\S.*)$", line)
        if m:
            name = m.group(1).strip()
            out.append(f"### {SECTION_LABELS.get(name, name)}")
        else:
            out.append(line)
    return "\n".join(out).strip()


def _previous_tag(current: str) -> str | None:
    """找比 current 更早的 release tag（用于 compare 链接）。找不到返回 None。"""
    status, tags = api("GET", "/tags?per_page=30")
    if status != 200 or not tags:
        return None
    names = [t["name"] for t in tags]
    if current in names:
        idx = names.index(current)
        return names[idx + 1] if idx + 1 < len(names) else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="用 CHANGELOG 补全 GitHub Release 说明")
    ap.add_argument("--tag", default="", help="tag 名（默认取最新 release）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不修改")
    args = ap.parse_args()

    if not os.environ.get("GH_TOKEN"):
        sys.exit("❌ 需要 GH_TOKEN 环境变量")

    if args.tag:
        status, rel = api("GET", f"/releases/tags/{args.tag}")
        if status != 200 or not rel:
            sys.exit(f"❌ 找不到 release {args.tag}：{rel}")
    else:
        status, rels = api("GET", "/releases?per_page=1")
        if status != 200 or not rels:
            sys.exit(f"❌ 读不到 releases：{rels}")
        rel = rels[0]

    tag = rel["tag_name"]
    version = tag.lstrip("v")
    print(f"Release：{tag}")

    section = changelog_section(version)
    if not section:
        sys.exit(f"❌ CHANGELOG.md 里找不到 [{version}] 这一节")

    # 变更历史链接：有上一版就对比上一版，否则指向该 tag 的提交列表
    prev_tag = _previous_tag(tag)
    if prev_tag:
        hist = f"https://github.com/{REPO}/compare/{prev_tag}...{tag}"
        hist_label = f"**完整变更历史**：{prev_tag} → {tag}"
    else:
        hist = f"https://github.com/{REPO}/commits/{tag}"
        hist_label = "**本版全部提交**"

    body = (
        translate_sections(section)
        + "\n\n"
        + INSTALL_SECTION.format(version=version)
        + f"\n{hist_label}：{hist}\n"
    )

    print("\n=== 将写入的 Release 说明 ===")
    print(body)
    print("=============================\n")

    if args.dry_run:
        print("（--dry-run 未修改）")
        return 0

    status, data = api("PATCH", f"/releases/{rel['id']}", {"body": body})
    if status != 200:
        sys.exit(f"❌ 更新失败：{data}")
    print(f"✅ 已更新 Release 说明：{rel['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
