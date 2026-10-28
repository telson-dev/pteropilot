# 真实面板实测指南（Roadmap「双面板兼容」）

`docs/compatibility.md` 记录了**代码核对**的端点；本文件说明如何在你自己的
Pterodactyl / Pelican 面板上做**真·实测**，把结果贴回 compatibility.md 作为
「已实测」标记。

## 1. 准备一个面板

- 已有面板：直接用（面板地址 + API 凭证）。
- 还没有：可以用官方 Docker 一键起一个测试面板（Pterodactyl 或 Pelican 的
  panel + wings 容器）。参考各面板官方文档；测试环境建议用最小配置。

## 2. 跑只读实测脚本

```bash
PANEL_URL=https://panel.example.com \
PANEL_API_KEY=ptlc_xxx \
PANEL_APP_KEY=ptla_xxx \
python scripts/live_smoke.py
```

脚本只发 GET，覆盖：

- 客户端 API：`client_servers` → `resources` → `console_log` → `list_files` → `list_backups`
- 应用 API：`list_nodes` / `list_all_servers` / `list_users`

输出示例：

```
面板识别: Pterodactyl（客户端密钥）
  ✅ client_servers (GET /api/client): 3 项，首项字段: uuid, attributes, ...
  ✅ resources (GET /servers/{id}/resources): 字段: current_state, cpu, ...
  ...
通过 8/8 个端点。全部通过——可把本报告贴进 docs/compatibility.md 的「已实测」栏。
```

## 3. 把结果归档

- 全部通过：在 `docs/compatibility.md` 顶部的「已实测记录」表里补一行（日期 / 面板 / 版本 / 通过数），
  并把面板类型/版本写进同一行；失败端点按下面的映射补进该表「失败端点」列与文末「已知差异」。
  端点命名对照见 compatibility.md 的「live_smoke 输出 ↔ 本表端点 映射」小节。
- 有失败：按错误信息排查，常见原因：
  - **403**：密钥角色不对（客户端工具用 `ptlc_`/`pacc_`；应用工具用 `ptla_`/`papp_`，见 `PANEL_APP_KEY`）。
  - **404**：面板版本过旧/过新，端点路径变了——把 404 端点记录进 compatibility.md 的差异表。
  - **超时**：面板 URL 或网络问题。

## 4. 注意事项

- 脚本**只读**：不触发电源/指令/写文件/备份。
- `download_file` 会返回短时签名 URL，属敏感输出——live_smoke 不探测该端点（避免打印签名）。
- Pelican 是 beta，版本之间 API 可能变：实测时记下面板版本号。
