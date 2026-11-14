"""用 GitHub REST API 推送本地提交（绕开不通的 github.com:443）。

背景：本机连 github.com 的 443 端口超时，但 api.github.com 可达。
git push 走前者所以一直失败。这里改用 Contents/Git Data API 把
「远程 HEAD → 本地 HEAD」的差异打成一个提交推上去。

做法（Git Data API）：
  1. 取远程 HEAD 的 commit / tree
  2. 用 git diff --name-status 找出相对远程变化的文件
  3. 变更/新增的文件 → create blob（base64）
     删除的文件 → 在 tree 里置 null
  4. create tree（base_tree = 远程 tree）
  5. create commit（parent = 远程 HEAD，message 用本地最新那条 + 说明）
  6. PATCH refs/heads/main 指向新 commit

只发内容、不改写本地 git 历史。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "telson-dev/pteropilot"
BRANCH = "main"
ROOT = Path(r"D:\Projects\pteropilot")
# 上次推送成功时本地的 HEAD。API 推上去的 commit 本地没有对象，
# 所以不能拿远程 sha 做 `git diff` 的基线——用这个文件记住。
BASE_FILE = ROOT / ".git" / "api-push-base"


def token() -> str:
    """从 git credential 里取 token（不落盘、不打印）。"""
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        check=False,
    )
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("拿不到 GitHub token")


TOKEN = token()


def api(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "telson-push",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        raise SystemExit(f"{method} {path} -> {e.code}: {detail}") from e


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        check=False,
    )
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {out.stderr[:300]}")
    return out.stdout


def main() -> int:
    remote_sha = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}")["object"]["sha"]
    local_sha = git("rev-parse", "HEAD").strip()
    print(f"远程 HEAD: {remote_sha[:7]}")
    print(f"本地 HEAD: {local_sha[:7]}")

    # 基线：上次推送过的本地 HEAD（远程 commit 本地没有对象，不能直接用）
    if BASE_FILE.exists():
        base = BASE_FILE.read_text(encoding="utf-8").strip()
    else:
        base = ""
    if not base or subprocess.run(
        ["git", "cat-file", "-e", base], cwd=str(ROOT), capture_output=True
    ).returncode != 0:
        # 兜底：远程对象本地若存在就用它，否则报清楚
        if subprocess.run(
            ["git", "cat-file", "-e", remote_sha], cwd=str(ROOT), capture_output=True
        ).returncode == 0:
            base = remote_sha
        else:
            raise SystemExit(
                f"没有可用基线。请把上次推送时的本地 commit 写进 {BASE_FILE}\n"
                f"（例如：echo <sha> > .git/api-push-base）"
            )
    print(f"diff 基线: {base[:7]}")

    # 相对基线变动的文件（本地视角）
    diff = git("diff", "--name-status", base, local_sha)
    changes: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0]  # A / M / D / R...
        if status == "R":
            changes.append(("D", parts[1]))
            changes.append(("A", parts[2]))
        else:
            changes.append((status, parts[1]))

    if not changes:
        print("没有差异，无需推送")
        return 0

    print(f"变更 {len(changes)} 个文件")
    base_tree = api("GET", f"/repos/{REPO}/git/commits/{remote_sha}")["tree"]["sha"]

    tree_entries = []
    for status, path in changes:
        if status == "D":
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            print(f"  删除 {path}")
            continue
        abs_path = ROOT / path
        if not abs_path.exists():
            print(f"  跳过（本地不存在）{path}")
            continue
        raw = abs_path.read_bytes()
        blob = api(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {"content": base64.b64encode(raw).decode(), "encoding": "base64"},
        )
        mode = "100755" if (abs_path.stat().st_mode & 0o111) else "100644"
        tree_entries.append(
            {"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]}
        )
        print(f"  {status} {path} ({len(raw)} B)")

    tree = api(
        "POST",
        f"/repos/{REPO}/git/trees",
        {"base_tree": base_tree, "tree": tree_entries},
    )

    subject = git("log", "-1", "--pretty=%s").strip()
    body = git("log", "-1", "--pretty=%b").strip()
    message = f"{subject}\n\n{body}\n\n(pushed via GitHub API: github.com:443 unreachable from this host)"
    commit = api(
        "POST",
        f"/repos/{REPO}/git/commits",
        {"message": message, "tree": tree["sha"], "parents": [remote_sha]},
    )
    print(f"新 commit: {commit['sha'][:7]}")

    api(
        "PATCH",
        f"/repos/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    BASE_FILE.write_text(local_sha, encoding="utf-8")
    print(f"✅ {BRANCH} 已更新 -> {commit['sha'][:7]}（基线记为本地 {local_sha[:7]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
