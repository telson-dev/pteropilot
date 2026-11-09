# 发布 pteropilot（两条独立轨道，互不冲突）

## 轨道 A：GitHub Release（打 tag 即自动出 wheel/sdist，无需手动）

`.github/workflows/release.yml` 已接管轨道 A——只要 `git push --tags v*`：
- 自动 checkout + setup-uv（Python 3.12）+ `uv sync --group dev`
- 跑 `uv run pytest --cov-fail-under=90` 作为发版前 sanity gate
- `uv build` 产出 wheel + sdist
- `softprops/action-gh-release` 把 `dist/*` 上传到对应 tag 的 GitHub Release（并自动生成 release notes）

> 说明 1 号：release.yml 只 pin 3.12，与 ci.yml 的 matrix `["3.12","3.13"]` 不冲突——
> 产物是 `py3-none-any` 纯 Python wheel，运行时版本无关；ci.yml 复数版本是为了**验证兼容性**，
> release.yml 单版本只是为了**构建产物 + 发版前跑一遍测试**，刻意不重复 matrix（省时）。无需对齐。

## 轨道 B：PyPI（需要账号 WinbowJi 的 API 令牌，手动）

# 1) 在 https://pypi.org/manage/account/token/ 创建（scope: entire account 或 project pteropilot）
# 2) 设置令牌后执行：
#    $env:UV_PUBLISH_TOKEN="pypi-..."   （PowerShell）
#    uv publish
# 产物已备好：dist/pteropilot-0.2.25-py3-none-any.whl + dist/pteropilot-0.2.25.tar.gz
# 发布后：uvx pteropilot 即装到 v0.2.25，PyPI 描述自动换成新版 README。

### 轨道 A 之后：补 Release 说明（可选但推荐）

`release.yml` 用 `generate_release_notes: true`，发布页描述只有一行 "Full Changelog" 链接。
执行下面的命令，会把 CHANGELOG 里该版本的章节搬进 Release 描述（含安装指引）：

```bash
python scripts/release.py            # 先用 API 打 tag（触发构建）
python scripts/sync_release_notes.py # 构建完成后补说明
```

## 两轨关系

- 轨道 A（GitHub Release）= 二进制分发 + 变更记录，走 `git push --tags`，**全自动**。
- 轨道 B（PyPI）= 包索引分发，让 `uvx pteropilot` 装到最新版，**手动 `uv publish`**。
- 两者产物来源相同（都是 `uv build` 的 dist/*），不互相覆盖、不产生双轨隐患；
  发一次新版本 = 先打 tag（触发明 A）→ 再 `uv publish`（手动轨道 B），两步各司其职。
