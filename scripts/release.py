#!/usr/bin/env python3
"""用 GitHub API 打 tag，触发 release.yml 自动构建并发布 GitHub Release。

为什么需要：`git push --tags` 在国内常被墙，而 release.yml 只在 push tag 时触发，
于是「代码更新了、但 GitHub 上永远没有新版本 Release」。这个脚本用 API 建 tag，
等于替你把被墙的那一步做了。

用法（PowerShell）：
    $env:GH_TOKEN = "<token>"
    python scripts/release.py                 # 按 pyproject.toml 版本创建 vX.Y.Z tag
    python scripts/release.py --dry-run       # 只检查，不打 tag
    python scripts/release.py --tag v0.2.23   # 指定 tag（默认取版本号前缀 v）

安全检查（踩过的坑，脚本会强制拦）：
  1) 远端仓库的 pyproject.toml 版本必须与本地一致——否则 tag 打出去，
     release.yml 会按**远端旧版本**构建，产物版本号错（曾出现 v0.2.22 里装的是 0.2.7 包）。
  2) 本地工作区不能有未提交改动（防止漏推）。
  3) tag 已存在则拒绝（避免覆盖已有发布）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "telson-dev/pteropilot"


def _retryable(exc: Exception) -> bool:
    """网络抖动类错误值得重试；HTTP 4xx/5xx 不重试（那是逻辑问题）。"""
    import http.client

    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    )


def api(method: str, path: str, body: dict | None = None, retries: int = 3) -> tuple[int, dict | None]:
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method)
    req.add_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pteropilot-release")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        req.add_header("Content-Type", "application/json")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, data, timeout=60) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return exc.code, {"error": raw[:300]}
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _retryable(exc) or attempt == retries - 1:
                raise
            import time as _time

            print(f"网络抖动（{type(exc).__name__}），{2 ** attempt} 秒后重试…", file=sys.stderr)
            _time.sleep(2 ** attempt)
    raise last or RuntimeError("api 调用失败")


def local_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("❌ pyproject.toml 里找不到 version")
    return m.group(1)


def remote_pyproject_version() -> str | None:
    import base64

    status, data = api("GET", "/contents/pyproject.toml")
    if status != 200 or not data:
        return None
    content = base64.b64decode(data["content"]).decode("utf-8", "replace")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    return m.group(1) if m else None


def _local_tree_sha() -> str | None:
    """本地 HEAD 的 tree sha：与远端 HEAD 的 tree sha 比对即可发现任何漏推。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _remote_tree_sha() -> str | None:
    status, ref = api("GET", "/git/ref/heads/main")
    if status != 200 or not ref:
        return None
    status, commit = api("GET", f"/git/commits/{ref['object']['sha']}")
    if status != 200 or not commit:
        return None
    return (commit.get("tree") or {}).get("sha")


def git_dirty() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return bool(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description="用 API 打 tag 触发 GitHub Release")
    ap.add_argument("--tag", default="", help="tag 名（默认 v<pyproject 版本>）")
    ap.add_argument("--dry-run", action="store_true", help="只做检查，不创建 tag")
    args = ap.parse_args()

    if not os.environ.get("GH_TOKEN"):
        sys.exit("❌ 需要 GH_TOKEN 环境变量")

    ver = local_version()
    tag = args.tag or f"v{ver}"
    print(f"本地版本：{ver}")
    print(f"目标 tag：{tag}")

    # 1) 版本一致性（关键检查）
    remote_ver = remote_pyproject_version()
    if remote_ver is None:
        sys.exit("❌ 读不到远端 pyproject.toml（仓库或权限问题）")
    print(f"远端版本：{remote_ver}")
    if remote_ver != ver:
        sys.exit(
            f"❌ 远端版本 {remote_ver} != 本地 {ver}：先把版本改动推上去，\n"
            f"   否则 tag 打出去会按旧版本构建（曾导致 v0.2.22 里装的是 0.2.7 包）"
        )

    # 2) 工作区干净
    if git_dirty():
        sys.exit("❌ 本地工作区有未提交改动，先提交/推送再发版")

    # 2b) 整棵树必须与远端一致——防止「版本号推了、别的文件漏推」导致发布内容不全
    local_tree = _local_tree_sha()
    remote_tree = _remote_tree_sha()
    if local_tree and remote_tree and local_tree != remote_tree:
        sys.exit(
            "❌ 本地文件树与远端 main 不一致：有文件还没推上去。\n"
            f"   本地 tree={local_tree[:12]}\n"
            f"   远端 tree={remote_tree[:12]}\n"
            "   先推送（push_repo.py）再发版，否则 tag 指向的代码会缺内容。"
        )

    # 3) tag 是否已存在
    status, _ = api("GET", f"/git/ref/tags/{tag}")
    if status == 200:
        sys.exit(f"❌ tag {tag} 已存在（要重发请先删除旧 tag 与 Release）")

    # 4) 远端 HEAD
    status, ref = api("GET", "/git/ref/heads/main")
    if status != 200 or not ref:
        sys.exit("❌ 读不到远端 main")
    head = ref["object"]["sha"]
    print(f"远端 main：{head[:7]}")

    if args.dry_run:
        print("\n✅ 检查全部通过（--dry-run 未创建 tag）")
        return 0

    status, data = api("POST", "/git/refs", {"ref": f"refs/tags/{tag}", "sha": head})
    if status not in (200, 201):
        sys.exit(f"❌ 创建 tag 失败：{data}")
    print(f"\n✅ tag 已创建：{tag}")
    print("   release.yml 会自动构建 wheel/sdist 并发布 GitHub Release（约 1-2 分钟）")
    print(f"   查看：https://github.com/{REPO}/actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
