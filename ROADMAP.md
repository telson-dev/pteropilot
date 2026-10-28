# Roadmap

PteroPilot 的功能路线图。这里只记「接下来做什么」和「为什么」，不写时间承诺——每一项都等它在真实使用中被验证、被需要之后才往前挪。

当前版本：**v0.2.5**（见 [CHANGELOG](CHANGELOG.md)）。

---

## 已上线（v0.x）

这些能力已经在 `uvx pteropilot` 里可用，也是后续一切扩展的地基：

- 服务器 / 节点列表：在线状态与资源占用
- 电源控制：启动 / 停止 / 重启 / 强杀（危险操作带二次确认）；批量电源（`batch_get_status` 状态摘要 + `batch_power` 批量开/停/重启，逐台循环、单台失败不阻断整体）
- **批量备份**：`batch_backup_status`（只读概览：每台备份数量/最近时间）+ `batch_backup`（整体确认后批量建备份，单台失败不阻断）
- 实时控制台：读日志、发指令
- 文件管理：列表 / 读取 / 写入 / 上传 / 重命名 / 删除 / 新建文件夹 / 一次性下载链接
- 备份：创建 / 列表 / 恢复 / 删除
- **查玩家在线**：`list_players`（日志解析，兼容原版 / Paper / Spigot / Forge / Fabric 中英文格式）
- **健康度与崩服检测**：`check_server_health`（状态级）+ `diagnose_crash`（日志级崩溃信号：内存不足 / JVM 崩溃 / 进程异常退出 / 致命错误，识别反复崩溃循环）
- 安全基线：只读模式开关、危险操作确认、API key 不落第三方

---

## 计划中

每一块都来自真实使用反馈或明确的用户需求，按优先级从高到低。

### 0. pteropilot Pro 客户端（另行开发）

- **诉求**：把面板管理能力打包成可视化客户端（付费订阅版）。
- **做什么**：Pro 客户端在**独立私有仓库**（`telson-dev/pteropilot-pro`）开发，**不开源**，本仓库只保留 MCP server 开源版。
- **要点**：Pro 可以合法依赖本仓库的 MIT 代码（保留版权声明即可），但 Pro 自身代码保持闭源。

### 1. 查玩家在线

- **状态**：已上线（`list_players`，见上方「已上线」与 CHANGELOG v0.2.2）。
- **遗留**：日志解析名单非权威（控制台只保留最近 ~1000 行），需要权威在线人数时建议配合 RCON / 面板资源接口核实。

### 2. 崩服告警

- **阶段一「可发现」已上线**：`check_server_health`（状态级）+ `diagnose_crash`（日志级崩溃信号检测），AI 已能立刻发现并响应。
- **阶段二「可配置通知」已上线**：`pteropilot-alerts` 命令只读扫描 + 钉钉/企业微信 webhook 推送，适合 cron 定时执行（见 README「Crash alerts」）。
- **阶段三「告警去重/冷却」已上线**：`--cooldown` 冷却期内同服务器同状态不重复推送、状态变化立即推送（状态持久化在 `~/.pteropilot/alerts_state.json`），防告警风暴。
- **阶段四「多渠道」已上线**：`--smtp-*`/`--mail-to` 邮件通道（465 SSL / 587 STARTTLS），与 webhook 可同时使用。
- **阶段五「连续确认阈值」已上线**：`--confirm-count N` 连续 N 次检测异常才推送（过滤瞬时抖动），恢复健康清零。
- **阶段六「按服务器级别配置」已上线**：`~/.pteropilot/alerts.json`（或 `--config`）按服务器覆盖 `confirm_count`/`cooldown_minutes`/`min_level`（优先级 服务器 > default > 命令行）。
- **阶段七「渠道独立开关与路由」已上线**：每服务器可配 `webhook`/`email` 开关、`webhook_url`/`mail_to` 路由到不同目标。
- **阶段八（计划中）**：批量备份/文件（等真实需求驱动）；双面板（Pelican）兼容深化。

### 3. 批量开服

- **诉求**：同时管好几个服务器时，逐台操作太累。
- **做什么**：把单台上的电源 / 备份 / 文件操作扩展到「一组服务器」维度。
- **状态**：电源维度已上线（`batch_get_status` 只读摘要 + `batch_power` 批量电源，见上方「已上线」）；备份 / 文件操作的批量化仍待真实需求驱动。
- **要点**：危险操作在多台联动时仍保持逐台确认或整体确认，绝不因「批量」放宽安全。

### 4. 双面板兼容深化（Pterodactyl + Pelican）

- **现状（本轮推进）**：端点清单已核对并写入 [docs/compatibility.md](docs/compatibility.md)（客户端 API 共用端点 + 应用 API 密钥分离 + 实测差异）。新增 `PANEL_APP_KEY` 应用密钥配置与启动密钥前缀诊断（`ptla_`/`ptlc_`/`papp_`/`pacc_`）。
- **下一步（待有真实面板环境）**：在运行中的 Pterodactyl / Pelican 面板上逐项实测全部端点与载荷，把「已实测」标记补进兼容文档。

### 5. 质量与测试

- **诉求**：MCP server 的可靠性和安全性，是一切的底线。
- **做什么**：持续补测试（错误路径、只读拦截、二次确认、非法输入）、守覆盖率、保持 `CHANGELOG` 与语义化版本同步。
- **要点**：安全审计结论持续归档进 [SECURITY.md](SECURITY.md)。

---

## 已否决 / 暂不做

为了让「做什么」更清楚，也明确记录「现在不做什么」：

- **面板内扩展（OAuth 以用户身份操作）路线**：暂不跟进，先专注独立 MCP server 这个形态。
- **面板周边集成（主题、模块市场等）**：当前不做，先把 MCP server 本身的服务器管理能力做扎实。

---

## 如何提需求

有想加的功能、或者哪个功能对你最要紧？开一个 issue（[github.com/telson-dev/pteropilot](https://github.com/telson-dev/pteropilot)），说清楚场景，我们据此调整优先级。

---

_a [telson](https://telson.dev) project_