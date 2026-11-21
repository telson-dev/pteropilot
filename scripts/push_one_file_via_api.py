"""只更新一个文件的 GitHub Contents API 脚本（请求数最少）。

背景：`push_via_api.py` 走 Git Data API 需要 7 次连续请求（get ref / commit /
tree / create blob / tree / commit / patch ref），本机网络下每次都可能超时，
累计下来几乎必然失败。

而「只改一两个文件」的场景用 Contents API 只需 2 次请求：
  GET /contents/<path>  → 拿到当前 blob sha
  PUT /contents/<path>  → 带上 sha 提交更新（GitHub 自动建 commit）

用法：
  python scripts/push_one_file_via_api.py --repo-dir <dir> --repo <owner/name> \\
      --branch main --path pteropilot.html
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from urllib.error import HTTPError

API = "https://api.github.com"
TIMEOUT = 90


def _token() -> str:
    import os

    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit("需要 GH_TOKEN 或 GITHUB_TOKEN 环境变量")
    return tok


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "pteropilot-push",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", required=True, help="本地 git 仓库目录")
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--path", required=True, help="仓库相对路径，如 pteropilot.html")
    ap.add_argument("--message", default="")
    a = ap.parse_args()

    repo_dir = Path(a.repo_dir)
    local_file = repo_dir / a.path
    if not local_file.is_file():
        print(f"✗ 本地没有这个文件：{local_file}", file=sys.stderr)
        return 1

    # 必须用 git 里的规范化内容（HEAD:<path>），不能读工作树：
    # 工作树在 Windows 上是 CRLF，而仓库里存的是 LF。直接读工作树会把
    # 行尾差异一起推上去，远端 blob 就和本地 git blob 不一致了（踩过）。
    content = subprocess.run(
        ["git", "show", f"HEAD:{a.path}"], cwd=repo_dir,
        capture_output=True,
    )
    if content.returncode != 0:
        print(f"✗ git show HEAD:{a.path} 失败：{content.stderr.decode('utf-8', 'replace')}",
              file=sys.stderr)
        return 1
    content = content.stdout
    message = a.message or subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo_dir,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip() or f"Update {a.path}"

    url = f"{API}/repos/{a.repo}/contents/{a.path}"
    # 1) 取当前 blob sha。远端还没有这个文件时是新建，不该再带 sha——
    #    直接失败（HTTPError 404）会让「第一次推送新文件」永远做不到。
    sha: str | None = None
    cur: dict = {}
    try:
        cur = _req("GET", f"{url}?ref={a.branch}")
        sha = cur.get("sha")
    except HTTPError as exc:
        if exc.code != 404:
            print(f"✗ 读取远端文件失败：HTTP {exc.code}", file=sys.stderr)
            return 1
        print(f"（远端还没有 {a.path}，按新建提交）")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 读取远端文件失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # 远端内容已经一致就不用再提交
    if sha:
        try:
            remote_bytes = base64.b64decode(cur.get("content") or "")
            if remote_bytes == content:
                print(f"✓ {a.path} 远端内容已一致，无需推送")
                return 0
        except Exception:  # noqa: BLE001 - 只做优化，不比对也照样推
            pass

    # 2) 提交更新
    body: dict = {
        "message": message,
        "content": base64.b64encode(content).decode(),
        "branch": a.branch,
    }
    if sha:
        body["sha"] = sha
    for attempt in range(1, 4):
        try:
            res = _req("PUT", url, body)
        except Exception as exc:  # noqa: BLE001
            print(f"  第 {attempt} 次失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        new_sha = (res.get("commit") or {}).get("sha") or "?"
        print(f"✅ {a.path} 已更新 -> {new_sha[:7]}（{res.get('commit', {}).get('html_url', '')}）")
        return 0
    print("✗ 三次都失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
