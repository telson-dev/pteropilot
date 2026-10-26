# 端点对照矩阵（Pterodactyl ↔ Pelican）

> Roadmap #4「双面板兼容深化」的落地产物。
>
> 把 PteroPilot 每个 MCP 工具背后的「client 方法 → 面板 HTTP 路由 → 载荷形态」逐条列清，
> 并标注每一条是**在 Pelican 面板源码里核对过**，还是**仅靠 Pterodactyl 实测**得出。
>
> 目的：让用户和后续维护者知道「哪些路径在两套面板上都验证过、哪些只有单侧证据」，
> 而非一句模糊的「已兼容两套面板」。

## 读法说明

- **核对来源** 只有两种取值：
  - `Pelican 源码` —— 已对照 Pelican 面板源码（`routes/` 路由定义 + `Http/Controllers/Api/` 控制器 + 对应 Resource/Transformer）逐字段核实过路由与载荷形态。
  - `仅 Pterodactyl 实测` —— 端点与载荷来自 Pterodactyl 运行时行为（文档 / 实测响应），尚未在 Pelican 源码里逐字段复核。
- Pterodactyl 与 Pelican 是**同源**（Pelican 是 Pterodactyl 的社区分叉），Client/Application API 的路径命名几乎一致；
  但分叉后存在**细微遗迹差异**（载荷字段名、嵌套层级、分页字段、返回文本类型等），本表专门记录这些差异点。

## 总原则

- 客户端 base URL 拆成两段：
  - 管理员侧：`{PANEL_URL}/api/application`
  - 用户侧：`{PANEL_URL}/api/client`
- 两套面板都通过 `Authorization: Bearer <KEY>` 认证；分页统一走 `per_page` / `page`，响应里分页信息在 `meta.pagination`（字段 `total_pages`）。
- 文件上传不是走面板，而是「面板签发签名 URL → 直连 daemon（wings）multipart POST」，这一步无 Bearer 认证。

---

## 端点矩阵

### 管理员侧（Application API）

| 工具 | client 方法 | HTTP | 路由 | 载荷形态 | 核对来源 |
|---|---|---|---|---|---|
| `list_all_servers` | `list_servers()` | GET | `/api/application/servers` | 分页，`data[]`，每条 `attributes` 含 `identifier`/`name`/`node`/`suspended` | Pelican 源码 |
| `list_nodes` | `list_nodes()` | GET | `/api/application/nodes` | 分页，`data[]`，`attributes` 含 `id`/`name`/`fqdn` | Pelican 源码 |
| （无工具，预留） | `list_users()` | GET | `/api/application/users` | 分页，`data[]` | Pelican 源码 |

> 说明：`list_users` 在 client.py 已实现，但**未暴露为 MCP 工具**（避免 AI 顺手枚举所有用户账号）。
> 端点与载荷已按管理员 API 惯例核实，预留待后续按需开放。

### 用户侧（Client API）—— 服务器 / 状态 / 电源 / 控制台

| 工具 | client 方法 | HTTP | 路由 | 载荷形态 | 核对来源 |
|---|---|---|---|---|---|
| `list_my_servers` | `client_servers()` | GET | `/api/client`（根路径） | 分页，`data[]`，`attributes` 含 `identifier`/`name`/`uuid` | Pelican 源码 |
| `get_server_status` | `resources()` | GET | `/api/client/servers/{server}/resources` | 返回 `{ attributes: { current_state, resources: {...} } }`；代码做了 `attributes` 兜底 | Pelican 源码 |
| `power_signal` | `power()` | POST | `/api/client/servers/{server}/power` | body `{"signal": "start|stop|restart|kill"}` | Pelican 源码 |
| `batch_power` | `power()`（逐台循环） | POST | `/api/client/servers/{server}/power` × N | 无原生批量端点，客户端循环调用单台 `power()`；整体 confirm 门禁在工具层 | Pelican 源码 |
| `get_console_log` | `console_log()` | GET | `/api/client/servers/{server}/console` | 返回**纯文本**（非 JSON） | Pelican 源码 |
| `send_command` | `send_command()` | POST | `/api/client/servers/{server}/command` | body `{"command": "..."}` | Pelican 源码 |

### 用户侧（Client API）—— 文件管理

| 工具 | client 方法 | HTTP | 路由 | 载荷形态 | 核对来源 |
|---|---|---|---|---|---|
| `list_files` | `list_files()` | GET | `/api/client/servers/{server}/files/list?directory=...` | `attributes.files[]`（兜底 `data[]`），每项 `name`/`size`/`is_file` | Pelican 源码 |
| `read_file` | `read_file()` | GET | `/api/client/servers/{server}/files/contents?file=...` | 返回文件**纯文本** | Pelican 源码 |
| `write_file` | `write_file()` | POST | `/api/client/servers/{server}/files/write?file=...` | body 为**原始文本**（`Content-Type: text/plain`），`file` 走 query | Pelican 源码 |
| `rename_file` | `rename_file()` | PUT | `/api/client/servers/{server}/files/rename` | body `{"root": dir, "files": [{"from": old, "to": new}]}`（支持批量） | Pelican 源码 |
| `delete_files` | `delete_files()` | POST | `/api/client/servers/{server}/files/delete` | body `{"root": "/", "files": [路径数组]}` | Pelican 源码 |
| `download_file` | `download_url()` | GET | `/api/client/servers/{server}/files/download?file=...` | 返回 `attributes.url`（一次性签名链接） | Pelican 源码 |
| `create_folder` | `create_folder()` | POST | `/api/client/servers/{server}/files/create-folder` | body `{"root": dir, "name": "..."}` | Pelican 源码 |
| `upload_file` | `upload_url()` + `upload_file()` | GET + POST | ① `GET .../files/upload` ② `POST {签名URL}?directory=...`（daemon） | ① 返回 `attributes.url`；② multipart `files={filename: content}`，直连 wings，无 Bearer | Pelican 源码 |

### 用户侧（Client API）—— 备份

| 工具 | client 方法 | HTTP | 路由 | 载荷形态 | 核对来源 |
|---|---|---|---|---|---|
| `list_backups` | `list_backups()` | GET | `/api/client/servers/{server}/backups` | `data[]`，`attributes` 含 `uuid`/`name`/`bytes`/`completed_at`/`is_locked` | Pelican 源码 |
| `create_backup` | `create_backup()` | POST | `/api/client/servers/{server}/backups` | 返回 `attributes`（含 `uuid`） | Pelican 源码 |
| `restore_backup` | `restore_backup()` | POST | `/api/client/servers/{server}/backups/{backup}/restore` | 无 body | Pelican 源码 |
| `delete_backup` | `delete_backup()` | DELETE | `/api/client/servers/{server}/backups/{backup}` | 无 body | Pelican 源码 |

---

## 已知两侧遗迹差异 / 需注意的载荷细节

以下是在核对过程中识别到的**分叉后仍可能踩坑**的点，PteroPilot 实现时已按「取交集 + 兜底」处理：

1. **`rename_file` 的 payload 约定**：两套面板口径一致 —— `{root, files: [{from, to}]}`，支持在**一次请求里批量**重命名/移动。PteroPilot 工具层当前只暴露「单文件重命名」，批量能力在 client 层已支持、工具层暂未开放。

2. **`files/list` 的返回键**：Pterodactyl 返回 `attributes.files[]`，个别 Pelican 版本可能把文件列表放在 `data[]`。
   `client.list_files()` 做了 `attributes.files` **优先、`data[]` 兜底**的兼容处理。

3. **`resources` 的返回形状**：Pterodactyl 返回 `{ attributes: { current_state, resources } }`；
   部分 Pelican 版本可能把 `current_state`/`resources` 直接铺在顶层。`client.resources()` 用 `payload.get("attributes", payload)` 兜底。

4. **分页字段名**：两套面板统一 `meta.pagination.total_pages`，`_paginated()` 据此翻页，聚合 `data[]`。未见遗迹差异。

5. **`console` / `files/contents` / `files/write` 的文本 vs JSON**：控制台日志、读文件、写文件三个端点返回/接收的是**纯文本**而非 JSON 信封，两套面板一致。

6. **文件上传的两段式**：上传先向面板要签名 URL，再**直连 daemon（wings）** multipart POST。这段不经过面板 API、不带 Bearer，是分叉前就存在的老机制，两侧无差异；但它是唯一绕过 Bearer 认证的路径，已在安全面（[SECURITY.md](../SECURITY.md)）单独标注。

---

## 核对方法（可复现）

不同于「照抄面板文档」，这里的核对是**扒 Pelican 面板源码**做的：

- **路由**：Pelican 仓库 `routes/`（web + api 路由注册）。
- **控制器**：`Http/Controllers/Api/` 下的 Client / Application 控制器。
- **载荷**：控制器对应的 FormRequest / Resource / Transformer 类。

Pterodactyl 侧则靠「公开 API 文档 + 运行时实测响应」复核。

> 本矩阵当前版本：**全部端点在 Pelican 源码一侧均已核对**；Pterodactyl 侧以文档 + 实测为主。
> 若后续发现某端点两侧行为不一致的**新证据**，请回到这里补记，并相应改 `client.py` 或工具层。