# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed
- **告警推送失败不再伪装成「查询失败」**：webhook/邮件发送失败时单独归入 `send_failed` / `send_errors`，输出醒目警告并让进程**退出码 3**（此前混在查询错误里、退出码仍 0——服务器真挂了、告警又没发出去时，用户会以为有监控实际没人收到，属最危险的静默失败）；一条都没发出时也不推进冷却状态，下次扫描会重试

### Added
- `pteropilot-alerts --reset`：清空冷却/连续确认状态（调完灵敏度想立刻验证、或服务器已修好但还卡在冷却期时用）；只动状态文件、不碰按服务器配置，支持 `--dry-run` 与 `--json`

### Changed
- 发布脚本（`scripts/release.py`、`scripts/sync_release_notes.py`）的 GitHub API 调用加入**指数退避重试**：网络抖动（RemoteDisconnected / 超时 / 连接错误）自动重试 3 次，HTTP 4xx/5xx 仍立即失败——曾因一次连接中断导致打 tag 半途失败

## [0.2.26] - 2026-11-11

### Changed
- `pteropilot --version` 在工具数明显偏少（<30）时额外提示「可能装到了旧版本（PyPI 发布可能滞后）」，避免用户以为功能缺失
- README 安装章节：补国内 PyPI 镜像（清华源）命令与 `--version` 自检方法；把国内常不可用的 git+GitHub 安装方式换成 GitHub Release 下载 `.whl`，并明确说明本项目不提供代理方案

### Fixed
- **面板地址自动规范化**：`PANEL_URL` 填成 `https://panel.example.com/api`（面板文档常见写法）时，原来会拼出 `/api/api/client` → 404，看着像密钥错；现在自动剥掉 `/api`、`/api/client`、`/api/application` 后缀与尾斜杠，真实子路径（如 `/pterodactyl`）保持不变。

## [0.2.25] - 2026-11-10

### Added
- `list_server_users` 子用户权限查看工具（第 42 个）：列出能管理该服务器的子用户——邮箱/用户名 + 被授予的权限项数与权限大类，回答「谁能动我的服」。只读。

## [0.2.24] - 2026-11-09

### Added
- `analyze_player_activity` 玩家活跃分析工具（第 41 个）：分析最近控制台日志里的加入/退出事件——活跃玩家排行（进/退次数）、高峰时段、日志末尾仍在线的人。只读，回答「谁最常来」「什么时候人多」；`top` 1-20 默认 10，并明确标注统计窗口受日志长度限制。
- `scripts/release.py` 整棵树一致性检查：本地 HEAD tree 必须等于远端 main tree，任何漏推文件都会在打 tag 前被拦下。

## [0.2.23] - 2026-11-09

### Added
- `scripts/sync_release_notes.py`：把 CHANGELOG 对应版本章节搬进 GitHub Release 描述（含安装指引与变更历史链接）——自动生成的 release notes 只有一行链接，用户看不出改了什么。
- `pteropilot --version`（也支持 `-V`/`-v`）：打印版本与**当前工具数**，不校验面板配置、不启动 server。用于确认装的是不是最新版（PyPI 可能滞后于 GitHub Release）。
- 发布自动化 `scripts/release.py`：用 GitHub API 打 tag 触发 `release.yml` 自动构建并发布 Release（绕开被墙的 `git push --tags`）；内置三道检查——远端 pyproject 版本必须与本地一致（防止 tag 打出旧版包）、工作区必须干净、tag 不可重复。

### Fixed
- `release.yml` 只上传 wheel 与 sdist：原来用 `dist/*` 会把 `uv build` 生成的 `.gitignore` 当成附件传到 Release。
- 修复 v0.2.22 Release 错版：远端 pyproject 版本曾停留在 0.2.7，导致 tag 指向的代码构建出旧版本包。

## [0.2.22] - 2026-11-09

### Added
- `list_schedules` 定时任务查看工具（第 40 个）：列出服务器在面板里配置的计划任务——cron 表达式、是否启用、仅在线时执行、上次/下次运行时间、每任务步骤数。只读，回答「自动重启配好了吗」「这个服有哪些定时任务」。
- `pteropilot-alerts --test` 通道自检：发一条测试消息验证 webhook/邮件配置是否正确（不读服务器、不写状态），失败时列出常见原因并以退出码 1 返回（便于脚本判断）。配置告警时先跑它，别等真崩服才发现没收到。
- Windows 一键装告警计划任务：`scripts/install_alerts_task.ps1`——一条命令建计划任务（默认每 5 分钟），自动生成包装脚本（面板凭据写进去，后台运行不依赖环境变量；中文日志 UTF-8 正常）、日志落 `~/.pteropilot/alerts.log`，支持 `-Status` / `-Remove` / `-DryRun` / `-Exe` 与邮件通道。解决 README 只有 cron 示例、Windows 服主不会配的问题。

## [0.2.21] - 2026-11-08

### Added
- `get_server_activity` 操作审计日志工具（第 39 个）：查看服务器事件流——谁在什么时候做了什么（电源、文件上传/删除、备份、指令），面板自身/计划任务触发的事件会标注「（系统）」。用来回答「谁重启了我的服务器」「谁删了这个文件」。只读，`limit` 1-100 默认 20。

## [0.2.20] - 2026-11-07

### Added
- `list_databases` 数据库查看工具（第 38 个）：列出服务器在面板里已分配的数据库——主机地址、库名、用户名、来源限制；只读，不改权限。适合配置 LuckPerms / CoreProtect 等需要 MySQL 的插件时查「数据库地址和账号」。密码不在客户端 API 返回范围内，工具会明确提示去面板查看。

## [0.2.19] - 2026-11-07

### Added
- `pteropilot-alerts --json` 机器可读输出：把扫描结果（含 `errors`/`channels`/`planned`）以 JSON 打印，便于 cron / 监控脚本解析（可与 `--dry-run` 组合）。

### Fixed
- `pteropilot-alerts` 面板不可达/密钥错误时不再抛 traceback：改为在 `errors` 里给出可读原因（`redact` 脱敏），退出码正常、`--json` 输出完整。

## [0.2.18] - 2026-11-07

### Added
- `pteropilot-alerts --dry-run` 演练模式：只打印「将会通知哪些服务器、走哪些渠道」，不实际推送/发邮件、也不写状态文件（不占用冷却/确认计数）——调阈值或首次开 cron 前先跑一遍，确认不会炸群。

## [0.2.17] - 2026-11-07

### Added
- `get_server_network` 网络分配查看工具：列出服务器 IP/端口（主地址 ⭐ 标记、附加端口、备注，优先显示 `ip_alias` 域名）——第 37 个工具，MC 服主查"服务器地址是多少"直接用。

## [0.2.16] - 2026-11-06

### Changed
- 写操作批量工具 `batch_power` / `batch_backup` 也支持 `server_ids=["*"]`（全部可访问服务器）；**安全设计**：`["*"]` 作用于全部服务器时任何电源信号都必须 `confirm=True`（即使是 start，防误启动全部）。

## [0.2.15] - 2026-11-06

### Changed
- 批量工具 `batch_list_players` / `batch_get_console_log` 支持 `server_ids=["*"]`（全部可访问服务器），与 `broadcast_message` 口径一致——"查所有服的在线玩家/日志"一句话搞定。

## [0.2.14] - 2026-11-05

### Changed
- `batch_get_status` 并发化：多台服务器状态改为 `asyncio.gather` 并发拉取（结果保序、单台失败仍不阻断），大数量服务器时更快（与其它批量工具统一）。

## [0.2.13] - 2026-11-05

### Added
- `check_panel_connection` 连接诊断工具：验证 PANEL_URL / API Key 可用性，失败按 401/403/404 给出可操作提示，成功报告可访问服务器数量与密钥角色警告——首次配置后先跑一次即可确认环境正确。

## [0.2.12] - 2026-11-05

### Added
- `search_server_logs` 日志搜索工具：在服务器日志里搜索关键字（不区分大小写，返回带行号的匹配行，只检索末尾 5000 行，limit 上限 200）——排查崩溃/报错时快速定位，配合 `read_server_logs` / `diagnose_crash` 使用。

## [0.2.11] - 2026-11-05

### Changed
- `batch_backup_status` 增强：每台显示备份总大小（人类可读），多台改为并发查询（单台失败仍不阻断）。
- `get_server_status` 网络流量格式化：rx/tx 由原始字节数改为人类可读（如 `1.0 MB / 2.0 MB`）。

## [0.2.10] - 2026-11-04

### Added
- `list_backups` 增强：备份大小格式化为人类可读（`1048576` → `1.0 MB`，自动 B/KB/MB/GB/TB），并显示创建日期。
- `list_files` 增强：文件大小同样格式化为人类可读；目录不再显示无意义的 `bytes` 值。

## [0.2.9] - 2026-11-04

### Added
- `get_server_status` 增强：面板返回配额时额外显示「内存上限 / 磁盘上限」（无配额字段时不显示）；在线时长格式化为人类可读（`3661000 ms` → `1 小时 1 分 1 秒`，支持天/小时/分/秒）。

## [0.2.8] - 2026-11-03

### Added
- `batch_list_players` 批量在线玩家工具：并发查看多台服务器当前在线玩家（只读，口径同 `list_players`——基于日志推断非权威；单台失败不阻断、结果保序）。
- `get_server_status` 增强：输出末尾新增「在线玩家: N（日志推断）」行（基于控制台日志，非权威；日志读取失败时静默省略，不影响主输出）。

## [0.2.7] - 2026-11-02

### Added
- `get_backup_download` 备份下载工具：获取备份的一次性下载链接（只读，配合 `list_backups` 的 UUID 使用）。
- `get_server_startup` 启动配置查看工具：显示服务器启动命令、egg 与启动变量（可见性/可编辑性标注，超过 30 个变量自动截断），排查启动失败用。
- `batch_get_console_log` 批量日志工具：并发读取多台服务器控制台最近日志（只读，单台失败不阻断、结果按输入顺序），适合多台同时出问题时的对比排查。
- `list_my_servers` 增强：新增 `with_status`（并发拉取每台实时状态并显示）与 `status` 过滤（如 `"running"` 只列在跑的服务器）。

### Changed
- 性能优化：批量工具并发化——`broadcast_message` / `batch_power` / `batch_backup` 由逐台串行改为 `asyncio.gather` 并发执行（结果保序、单台失败仍不阻断其余），多台场景耗时从 N× 单台延迟降至 ≈1×。

## [0.2.6] - 2026-11-02

### Added
- `broadcast_message` 批量广播工具：向多台 / 全部服务器控制台广播同一条 `say <message>`（Minecraft 聊天公告，如停机维护公告）；`server_ids` 传 `["*"]` 表示全部可访问服务器，只读拒绝 + confirm 拦截（高危写操作）+ 单台失败不阻断其余，逐台回馈成功/失败清单。
- `read_server_logs` 日志排查工具：读取游戏服务器日志末尾若干行（默认 `logs/latest.log` 最后 200 行，上限 5000 行），内容过长自动只保留末尾 20000 字符（最新日志），配合 `diagnose_crash` 排查崩溃与报错。
- `player_history_stats` 玩家在线历史统计工具：解析控制台日志（join / left / lost connection），输出当前在线（推断）、上线次数 Top、最近加入/离开活动，用于了解服务器活跃度（非权威，基于最近日志行推断）。
- `schedule_restart` 计划重启工具：先向控制台广播公告（say "服务器将在 N 分钟后自动重启..."，可自定义），N 分钟后自动执行 restart；minutes 钳制 1-120，高危写操作需 confirm=True；计划随 MCP 进程存活（进程退出则取消，无持久化）。

## [0.2.5] - 2026-10-28

### Added
- 真实面板实测工具 `scripts/live_smoke.py`：对真实 Pterodactyl / Pelican 面板做只读端到端探测并输出报告（识别面板类型与密钥角色，仅发 GET、绝不触发写操作）；配套全栈 HTTP 集成测试 `tests/test_live_smoke.py`（uvicorn 模拟器，套件首个非 MockTransport 测试）与 [docs/live-testing.md](docs/live-testing.md) 实测指南（Roadmap「双面板兼容深化」落地）。

### Changed
- `diagnose_crash` 崩溃关键词表扩充：新增「看门狗超时（tick 卡死）」（Paper/Spigot watchdog、中文「看门狗」）与「原生崩溃（SIGSEGV/segfault）」两类信号。明确排除 `Can't keep up!`（WARN 负载警告）与 `Connection reset by peer`（网络错误）等易误报项，保持「宁漏报不误报」原则。

### Security
- 密钥脱敏加固：新增 `client.redact()` 对 webhook 签名 query（钉钉 `access_token=`、企业微信 `[?&]key=`）的统一脱敏，防止带签名的机器人 URL 在推送失败时随 `httpx` 异常信息/`errors` 泄露；补充模块级纯函数单测（`tests/test_guarded.py`）。

## [0.2.4] - 2026-10-28

### Added
- 新增 `pteropilot-alerts` 命令（`pteropilot.alerts` 模块）：只读扫描全部可访问服务器，异常/崩溃（状态级 `check_server_health` + 日志级 `diagnose_crash`）时向钉钉/企业微信机器人 webhook 推送通知；`--min-level`/`--token`/`--timeout`/`--no-log-signals` 可配，适合 cron 定时执行（Roadmap「崩服告警」阶段二：可配置通知通道）。
- `pteropilot-alerts` 新增告警冷却去重（`--cooldown <分钟>`，状态存 `~/.pteropilot/alerts_state.json`）：同一服务器同状态下冷却期内不重复推送，状态变化立即推送——防告警风暴（Roadmap「崩服告警」阶段三）。
- `pteropilot-alerts` 新增**邮件渠道**（`--smtp-host/--smtp-user/--smtp-pass/--mail-to`，465 SSL / 587 STARTTLS；环境变量 `SMTP_*`/`MAIL_TO` 可配）：与 webhook 可同时使用，双通道互备（Roadmap「崩服告警」阶段四）。
- `pteropilot-alerts` 新增**连续确认阈值**（`--confirm-count N`，环境变量 `CONFIRM_COUNT` 可配）：同一服务器连续 N 次检测到异常才推送，过滤瞬时抖动误报；恢复健康自动清零计数（Roadmap「崩服告警」阶段五）。
- `pteropilot-alerts` 新增**按服务器级别配置**（`~/.pteropilot/alerts.json` 或 `--config`）：`default` 段 + 每服务器覆盖 `confirm_count`/`cooldown_minutes`/`min_level`，优先级 服务器 > default > 命令行参数（Roadmap「崩服告警」阶段六）。修正冷却语义：未达阈值的「确认中」观察不再推进通知时钟，冷却从上次真正推送起算。
- `pteropilot-alerts` 新增**渠道按服务器独立开关与路由**：每服务器可配 `webhook`/`email` 布尔开关、`webhook_url`（指向其他机器人）、`mail_to`（发给其他收件人）（Roadmap「崩服告警」阶段七）。
- 新增 `batch_backup_status`（只读）：遍历可访问服务器列出备份数量与最近备份时间，单台失败不阻断（Roadmap「批量」收尾）。
- 新增 `batch_backup`（写）：批量创建备份，整体二次确认 + 只读拒绝，逐台创建、单台失败不阻断其余。
- 双面板兼容：新增 `PANEL_APP_KEY`（应用 API 专用密钥，未设时回退 `PANEL_API_KEY`）+ 启动密钥前缀诊断（`ptla_`/`ptlc_`/`papp_`/`pacc_` 角色错配提前提示）；新增 [docs/compatibility.md](docs/compatibility.md) 记录两套面板端点点核对与实测差异（Roadmap「双面板兼容深化」）。

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
