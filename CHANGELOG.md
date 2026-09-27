# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
