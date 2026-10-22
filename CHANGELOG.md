# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
