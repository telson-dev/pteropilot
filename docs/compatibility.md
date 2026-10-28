# 双面板兼容（Pterodactyl + Pelican）

PteroPilot 同时面向 Pterodactyl 1.x 与 Pelican（社区活跃分支）两套面板。
本文件记录端点的核对结论与两套面板的已知差异，来源分两类：

- **实测**：第三方集成方（Provibr）在真实运行的面板上逐项测量，2026-08 数据，
  链接见文末；我们的实现按这些差异做了对应处理。
- **代码核对**：本仓库 `src/pteropilot/client.py` 中的端点路径，与两套面板
  的既有客户端/控制器约定一致（Pterodactyl v1 / Pelican 1.0.0-beta）。

## 已实测记录（本仓 `live_smoke.py` 实测，与 `live-testing.md` 联动）

`docs/live-testing.md` 指导你用自己的面板跑 `scripts/live_smoke.py`，把结果贴回本文件作为「已实测」标记。
该脚本只发 GET、覆盖 8 个只读端点，输出以下端点的成功/摘要。**实测通过后，在下方表格补/更新一行**：

> 贴回格式 → `已实测：<日期>，<面板：Pterodactyl/Pelican> vX，通过 N/8`（面板 version 用面板 `/api/client/account` 或登录页可见的版本号）。

| 日期 | 面板 / 版本 | 通过 | 失败端点（如有） | 备注 |
|---|---|---|---|---|
| （示例）2026-08 | Pterodactyl 1.11.x（Provibr 第三方） | 8/8 | — | 见文末参考链接 |
| _待你的实测_ | | | | |

> 本仓目前**尚未用自己的面板跑过 live_smoke**（已就绪的实测工具见 `scripts/live_smoke.py`），上方仅列的第三方示例。
> 你跑完一次后，把「日期 / 面板 / 版本 / 通过数」填进空白行，并把失败端点的 404/403 差异补进文末「已知差异」。

### live_smoke 输出 ↔ 本表端点 映射

`live_smoke.py` 报告用的是 **HTTP 路由名**，本文件下文的端点表用小标题分组、行内列 **client 方法名**。二者是同一批端点，对照如下（贴回时可据此逐行核对，避免 A 报告对不上 B 表）：

| live_smoke 输出（路由名） | compatibility 端点表（分组 → 行） |
|---|---|
| `client_servers (GET /api/client)` | 客户端 API 表 → `client_servers` 行 |
| `resources (GET /servers/{id}/resources)` | 客户端 API 表 → `resources` 行 |
| `console_log (GET /servers/{id}/console)` | 客户端 API 表 → `console_log` 行 |
| `list_files (GET /servers/{id}/files/list)` | 客户端 API 表 → 「文件列表/读取」行 |
| `list_backups (GET /servers/{id}/backups)` | 客户端 API 表 → 「备份」行（`GET .../backups`） |
| `list_nodes (GET /api/application/nodes)` | 应用 API 表 → `list_all_servers`/`list_users`/`list_nodes` 行 |
| `list_all_servers (GET /api/application/servers)` | 应用 API 表 → 同上（`list_servers`） |
| `list_users (GET /api/application/users)` | 应用 API 表 → 同上（`list_users`） |

> 来源归并说明：下文的「来源」分两类——**实测**（第三方 Provibr，2026-08）与**代码核对**。
> 你自己的 live_smoke 结果落到上方的「已实测记录」表即可，无需改动下方逐行来源；若实测发现某行与代码核对结论不符，
> 请回到该行更新备注并把差异补进文末「已知差异」。

## 密钥体系（重要）

| 角色 | Pterodactyl | Pelican | 说明 |
|---|---|---|---|
| 客户端密钥（Client API） | `ptlc_` | `pacc_` | 电源/控制台/文件/备份等核心工具 |
| 应用密钥（Application API） | `ptla_` | `papp_` | `list_nodes` / `list_all_servers` / `list_users` |

- 两套面板**客户端与应用密钥是分开的两把**，一把钥匙无法同时驱动两套 API。
- PteroPilot 配置：
  - `PANEL_API_KEY` = 客户端密钥（核心工具必需）
  - `PANEL_APP_KEY` = 应用密钥（可选；不设时应用类工具在 **Pterodactyl 上会 403**，
    **Pelican 上会返回错误结果**——Pelican 会接受客户端密钥访问应用 API，但语义错误）
- 启动时会做密钥前缀诊断（`key_role_warnings`），把这类易踩的坑提前打印出来。

## 客户端 API 端点（两套面板共用，代码已核对）

| 工具 | 端点 | 备注 |
|---|---|---|
| `client_servers` | `GET /api/client`（分页） | |
| `resources` | `GET /api/client/servers/{id}/resources` | 两套面板该端点**缓存约 20 秒**，轮询太快会拿到旧值 |
| `power` | `POST /api/client/servers/{id}/power` | `{"signal": "start\|stop\|restart\|kill"}` |
| `console_log` | `GET /api/client/servers/{id}/console` | 纯文本返回 |
| `send_command` | `POST /api/client/servers/{id}/command` | `{"command": "..."}` |
| 文件列表/读取 | `GET .../files/list` / `GET .../files/contents` | |
| 写/重命名/删除/建目录 | `POST/PUT .../files/write|rename|delete|create-folder` | 载荷形态两套一致 |
| 下载/上传 | `GET .../files/download` / `GET .../files/upload` + wings 直连 | |
| 备份 | `GET/POST .../backups`、`POST .../backups/{id}/restore`、`DELETE .../backups/{id}` | |

## 应用 API 端点（需 `PANEL_APP_KEY`）

| 工具 | 端点 |
|---|---|
| `list_all_servers` / `list_users` / `list_nodes` | `GET /api/application/servers` / `/users` / `/nodes`（分页） |

## 已知差异（实测，来源见文末）

- **Nests 已删除**（Pelican）：PteroPilot 不读取 egg/nest，不受影响。
- **`/api/client/account` 的 `id` 字段被移除**（Pelican）：PteroPilot 不用该端点，不受影响。
- **节点 CPU 上报**（Pelican 才有，按单核百分比）：`list_nodes` 展示的资源字段两套面板不完全一致，展示层按「缺什么显示什么」处理。
- **`limits.io = 0`**（Pterodactyl 静默变 500 / Pelican 接受）：PteroPilot 不创建服务器，不受影响。

## 参考

- [Provibr：Pterodactyl vs Pelican（九个实测 API 差异）](https://provibr.com/blog/pterodactyl-vs-pelican)
- [Provibr：Choosing a game panel by the API you automate](https://provibr.com/blog/choosing-a-game-panel-by-its-api)
