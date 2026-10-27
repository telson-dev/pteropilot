# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 新增 `pteropilot-alerts` 命令（`pteropilot.alerts` 模块）：只读扫描全部可访问服务器，异常/崩溃（状态级 `check_server_health` + 日志级 `diagnose_crash`）时向钉钉/企业微信机器人 webhook 推送通知；`--min-level`/`--token`/`--timeout`/`--no-log-signals` 可配，适合 cron 定时执行（Roadmap「崩服告警」阶段二：可配置通知通道）。
- `pteropilot-alerts` 新增告警冷却去重（`--cooldown <分钟>`，状态存 `~/.pteropilot/alerts_state.json`）：同一服务器同状态下冷却期内不重复推送，状态变化立即推送——防告警风暴（Roadmap「崩服告警」阶段三）。
- `pteropilot-alerts` 新增**邮件渠道**（`--smtp-host/--smtp-user/--smtp-pass/--mail-to`，465 SSL / 587 STARTTLS；环境变量 `SMTP_*`/`MAIL_TO` 可配）：与 webhook 可同时使用，双通道互备（Roadmap「崩服告警」阶段四）。
- `pteropilot-alerts` 新增**连续确认阈值**（`--confirm-count N`，环境变量 `CONFIRM_COUNT` 可配）：同一服务器连续 N 次检测到异常才推送，过滤瞬时抖动误报；恢复健康自动清零计数（Roadmap「崩服告警」阶段五）。
- `pteropilot-alerts` 新增**按服务器级别配置**（`~/.pteropilot/alerts.json` 或 `--config`）：`default` 段 + 每服务器覆盖 `confirm_count`/`cooldown_minutes`/`min_level`，优先级 服务器 > default > 命令行参数（Roadmap「崩服告警」阶段六）。修正冷却语义：未达阈值的「确认中」观察不再推进通知时钟，冷却从上次真正推送起算。
- `pteropilot-alerts` 新增**渠道按服务器独立开关与路由**：每服务器可配 `webhook`/`email` 布尔开关、`webhook_url`（指向其他机器人）、`mail_to`（发给其他收件人）（Roadmap「崩服告警」阶段七）。
- 新增 `batch_backup_status`（只读）：遍历可访问服务器列出备份数量与最近备份时间，单台失败不阻断（Roadmap「批量」收尾）。
- 新增 `batch_backup`（写）：批量创建备份，整体二次确认 + 只读拒绝，逐台创建、单台失败不阻断其余。

## [0.2.3] - 2026-10-26

### Added
- 新增 `diagnose_crash` 工具：结合当前状态与最近控制台日志，扫描崩溃信号（内存不足 / JVM 崩溃 / 进程异常退出 / 致命错误），输出检测结果、命中行数与建议动作；识别反复崩溃循环。只读、不发外部告警（Roadmap「崩服告警」阶段一「可发现」收尾）。
- 新增 `batch_power` 工具：批量对一组服务器发电源信号（start/stop/restart/kill）。面板无原生批量电源端点，退化为「逐台循环调用单台 power + 整体二次确认」；只读模式下整体拒绝、非法 signal 整体拒绝、stop/restart/kill 需整体 confirm=True（start 免确认）、单台失败单独标注不阻断其余（Roadmap「批量开服」写路径收尾）。

## [0.2.2] - 2026-10-24

### Added
- 新增 `list_players` 工具：从控制台日志解析玩家加入/退出，返回当前在线人员名单；兼容 Minecraft 原版 / Paper / Spigot / Forge / Fabric 的中英文日志格式（Roadmap「查玩家在线」第一优先项）。
- 新增 `check_server_health` 工具：只读对比 `current_state` 判定健康度（healthy/transitional/abnormal/unknown），把离线/错误/挂起等异常态翻译成中文提示与建议动作，供 AI 判断是否告警（Roadmap「崩服告警」第二优先项）。
- 新增 `batch_get_status` 工具：遍历我可访问的服务器，逐台返回「标识 | 名称 | 状态 | 健康等级」摘要；只读纯摘要、不扩展危险操作，单台超时/失败单独标注为「查询失败」不阻断整体（Roadmap「批量开服」第三优先项骨架）。

### Fixed
- MCP 握手上报真实版本号 `0.2.1`（此前回退到 mcp 框架自身的 1.30.0）。
- 面板错误中文化：新增 `_Guarded` 包装 httpx 客户端，把 401/403/404/405/422/429/5xx 以及连接失败、超时统一转成中文 `PanelError`，18 个工具无需各自 try/except。
- 缺少 `PANEL_URL`/`PANEL_API_KEY` 时打印中文引导并 `exit(1)`，不再抛 traceback。

## [0.2.1] - 2026-10-18

### Added
- 新增 `get_console_log` 工具：读取服务器控制台最近日志，补全「发指令 → 看回馈」闭环。

### Changed
- `stop` 电源操作纳入二次确认（与 restart/kill 一致；start 免确认）。
- `PANEL_READONLY` 容错增强：`1/true/yes/on`（大小写、空格不敏感）均能开启只读模式。

### Fixed
- 修复 SECURITY.md 漏洞报告链接的相对路径 404。

## [0.2.0] - 2026-09-30

### Fixed
- 修复 `get_server_status`：`current_state` 误从 `resources` 子对象读取（实际在 `attributes` 顶层），导致状态恒显示「?」。
- 修复 `rename_file` 请求体：改为面板约定 `{root, files: [{from, to}]}`（原 `{directory, from, to}` 两个面板均不识别）。
- 客户端服务器列表现在正确遍历分页（此前仅取第一页，超过单页会漏服务器）。

### Added
- Pelican 兼容性验证：全部端点与 payload 对照 Pelican 源码（routes/ 与 Http/Controllers/Api/）核实，并补充 Pelican 形状的离线测试用例。
- 新增 4 个文件管理工具：`upload_file`（base64 内容，两步上传直连 daemon）、`download_file`（一次性下载链接）、`create_folder`、`rename_file`（此前 client 有原语但未暴露）。

## [0.1.0] - 2026-09-27

首个开发版本。

### Added
- 服务器列表（个人可访问 / 管理员全量）与节点列表
- 实时状态与资源占用查询（CPU / 内存 / 磁盘 / 网络 / 在线时长）
- 电源控制：start / stop / restart / kill
- 控制台指令发送
- 文件管理：列表、读取、写入、删除
- 备份：列表、创建、恢复、删除
- 只读模式（`PANEL_READONLY=1`）
- 高危操作二次确认（`confirm=True`）
- 中英双语 README
- 基于 pytest + httpx MockTransport 的离线测试
