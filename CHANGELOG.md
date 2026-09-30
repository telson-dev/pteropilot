# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed
- 修复 `get_server_status`：`current_state` 误从 `resources` 子对象读取（实际在 `attributes` 顶层），导致状态恒显示「?」。
- 修复 `rename_file` 请求体：改为面板约定 `{root, files: [{from, to}]}`（原 `{directory, from, to}` 两个面板均不识别）。
- 客户端服务器列表现在正确遍历分页（此前仅取第一页，超过单页会漏服务器）。

### Added
- Pelican 兼容性验证：全部端点与 payload 对照 Pelican 源码（routes/ 与 Http/Controllers/Api/）核实，并补充 Pelican 形状的离线测试用例。

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
