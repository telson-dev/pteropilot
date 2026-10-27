# 双面板兼容（Pterodactyl + Pelican）

PteroPilot 同时面向 Pterodactyl 1.x 与 Pelican（社区活跃分支）两套面板。
本文件记录端点的核对结论与两套面板的已知差异，来源分两类：

- **实测**：第三方集成方（Provibr）在真实运行的面板上逐项测量，2026-08 数据，
  链接见文末；我们的实现按这些差异做了对应处理。
- **代码核对**：本仓库 `src/pteropilot/client.py` 中的端点路径，与两套面板
  的既有客户端/控制器约定一致（Pterodactyl v1 / Pelican 1.0.0-beta）。

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
