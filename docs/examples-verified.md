# 文档示例实测清单（防文档漂移）

> 实测日期：2026-10-22 · 版本：v0.2.1 · 仓库：D:\Projects\pteropilot

对照 `README.md` / `README.zh-CN.md` / `docs/README.md` 中的全部代码示例逐条实测，
确认能真运行、命令名与参数与当前 v0.2.1 一致。以下每一项均附实测命令与结果。

## 结论速览

| # | 示例 | 结果 |
|---|---|---|
| 1 | `uvx pteropilot`（安装方式）入口脚本 | ✅ 通过 |
| 2 | `uv run pteropilot`（源码运行） | ✅ 通过 |
| 3 | 无 `PANEL_URL`/`PANEL_API_KEY` 时的友好报错 | ✅ 通过（EXIT=1，中文提示） |
| 4 | MCP 协议握手 / 版本上报 | ✅ 通过（version 0.2.1） |
| 5 | 环境变量表与默认值 | ✅ 通过 |
| 6 | 20 个工具名与 docs 工具一览表 | ✅ 一致（无漂移） |
| 7 | 测试方式 `uv run pytest` | ✅ 通过（93 passed） |

**结论：无文档漂移，示例全部实测通过，无需修文档或代码。**

---

## 实测明细

### 1. 安装方式入口脚本（对应 `uvx pteropilot`）

pyproject 声明 `[project.scripts] pteropilot = "pteropilot.server:main"`；
构建 wheel 后入口脚本为 `pteropilot.exe`（Unix 为 `pteropilot`）。

```
$ uv build --wheel
Successfully built dist\pteropilot-0.2.1-py3-none-any.whl
$ .venv\Scripts\pteropilot.exe   # 无 env 时
PteroPilot 启动失败：需要设置 PANEL_URL 和 PANEL_API_KEY 环境变量
...
EXIT=1
```

结论：`uvx pteropilot` 对应的入口脚本存在且可执行，与文档命令一致。

### 2. 源码运行（对应 `uv run pteropilot`）

```
$ uv run python -c "import pteropilot; print(pteropilot.__version__)"
0.2.1
$ uv run pteropilot   # 无 env 时 -> 同上友好报错，EXIT=1
```

结论：`uv run pteropilot` 命中 `src/pteropilot/server.py` 的 `main()`，正常。

### 3. 缺环境变量的友好提示（FAQ Q1）

```
$ uv run pteropilot   # PANEL_URL / PANEL_API_KEY 均空
PteroPilot 启动失败：需要设置 PANEL_URL 和 PANEL_API_KEY 环境变量

请在 MCP 客户端的配置里补上这两个环境变量：
    "env": {
      "PANEL_URL": "https://panel.example.com",
      "PANEL_API_KEY": "ptlc_xxxxxxxx"
    }
面板地址从浏览器地址栏复制；API Key 在面板「账户 → API 凭证」里生成。
```

结论：与 `docs/README.md` FAQ Q1 描述完全一致。

### 4. MCP 协议握手 / 版本上报

配置好 env 后，通过 stdio 发送 `initialize` 请求：

```
$ echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | pteropilot.exe
{"jsonrpc":"2.0","id":1,"result":{... "serverInfo":{"name":"pteropilot","version":"0.2.1"}}}
```

结论：server 能正常启动并响应协议，上报版本号 0.2.1 与 `__version__` 一致。

### 5. 环境变量

| 变量 | 文档描述 | 实现（config.py） | 一致 |
|---|---|---|---|
| `PANEL_URL` | 面板地址，不带尾随斜杠 | `os.environ["PANEL_URL"]`，`rstrip("/")` | ✅ |
| `PANEL_API_KEY` | 面板 API Key | `os.environ["PANEL_API_KEY"]`（`repr=False`） | ✅ |
| `PANEL_READONLY` | `1` 开启只读；`1/true/yes/on` 均生效 | `.strip().lower() in ("1","true","yes","on")` | ✅ |

### 6. 工具清单对齐

- server.py 实测 `@mcp.tool()` 数量：**20**。
- `docs/README.md`「工具一览」表行数：**20**。
- 逐一核对：`list_my_servers` / `list_all_servers` / `list_nodes` / `get_server_status`
  / `list_players` / `list_files` / `read_file` / `download_file` / `list_backups` / `get_console_log`
  / `power_signal` / `send_command` / `write_file` / `delete_files` / `create_folder`
  / `rename_file` / `upload_file` / `create_backup` / `restore_backup` / `delete_backup`
  全部一致（`client.py` 有 `list_users` 方法但未暴露为工具，文档亦未列出，无漂移）。

### 7. 测试方式

```
$ uv run pytest -q
93 passed, 1 warning in 4.45s
```

结论：pyproject 已含 `[dependency-groups].dev`（pytest/pytest-asyncio/pytest-cov），裸 `uv run pytest` 可复现。

---

> 注：本文是 2026-10-22 的历史实测快照（当时版本 v0.2.1）。当前发布版已随 0.2.4 提升，工具数增至 24，最近的实测以 `docs/README.md` 工具表与 `docs/endpoint-matrix.md` 为准。

## 备注

- `dist/` 中仍存有历史 `pteropilot-0.2.0.*` 构建产物，不影响文档正确性；发布前需清理或忽略。
- 本次实测未真连面板（无真实 PANEL_URL），网络层行为由既有 `tests/test_guarded.py`、`tests/test_server_tools.py` 覆盖。