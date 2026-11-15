"""把 CHANGELOG 里跨多个版本的段落合并成一个 Release 说明（一次性修复用）。

背景：这次 v0.2.33 的 Release 实际涵盖了 v0.2.28~v0.2.33 六个版本
（中间几个版本没单独打 tag），而 release.yml 的 generate_release_notes
只会写一行 compare 链接，sync_release_notes.py 又只搬最新那一节。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO = "telson-dev/pteropilot"
TAG = "v0.2.33"
SINCE = "0.2.27"          # 上个 Release 的版本，不含
CHANGELOG = Path(r"D:\Projects\pteropilot\CHANGELOG.md")
TOK = os.environ["GH_TOKEN"]


def api(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    print(f"  {method} {url}")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {TOK}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "telson",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:300]}")
        raise


def key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def norm(body: str) -> str:
    for a, b in (
        ("### Added", "### 新增"),
        ("### Fixed", "### 修复"),
        ("### Changed", "### 变更"),
        ("### Removed", "### 移除"),
        ("### Security", "### 安全"),
    ):
        body = body.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def main() -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    parts = re.split(r"^## \[(\d+\.\d+\.\d+)\][^\n]*\n", text, flags=re.M)
    sections = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}

    want = sorted((v for v in sections if key(v) > key(SINCE)), key=key, reverse=True)
    print("将包含版本:", ", ".join(want))
    if not want:
        raise SystemExit("CHANGELOG 里没有找到目标版本段落")

    blocks = [f"## v{v}\n\n{norm(sections[v])}" for v in want]
    body = "\n\n".join(blocks)
    body += (
        "\n\n## 安装\n\n```bash\n"
        f"# 从本 Release 的 wheel（推荐；PyPI 上的包可能滞后）\n"
        f"pip install pteropilot-{want[0]}-py3-none-any.whl\n\n"
        "# 或从包索引（若已同步）\nuv tool install pteropilot\n```\n\n"
        f"装好可自检：`pteropilot --version`（应为 `v{want[0]} "
        f"({45} MCP tools)`）。\n\n"
        f"**完整变更**：https://github.com/{REPO}/compare/v{SINCE}...{TAG}\n"
    )

    # 注意：GitHub 没有 PATCH /releases/tags/{tag}（那个只有 GET）。
    # 改描述必须拿到数字 id：GET by tag -> PATCH /releases/{id}
    rel = api("GET", f"/repos/{REPO}/releases/tags/{TAG}")
    api("PATCH", f"/repos/{REPO}/releases/{rel['id']}", {"body": body})
    print(f"✅ 已写入 {len(body)} 字符，覆盖 {len(want)} 个版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
