# PteroPilot

[English](README.md) | **中文**

![PteroPilot](assets/pteropilot-icon.png)

**用自然语言管你的游戏服务器。**
PteroPilot 是一个开源 MCP server，让 Claude、Cursor 等 AI 助手直接管理 [Pterodactyl](https://pterodactyl.io) / [Pelican](https://pelican.dev) 面板上的游戏服务器。

> 「帮我看看生存服什么状态，卡的话重启一下，顺便看看最近有没有自动备份。」

——不用登录面板，不用翻日志，一句话的事。

## 能做什么

- 📊 查看服务器列表、在线状态、资源占用
- ⚡ 电源控制：启动 / 停止 / 重启 / 强杀
- 🖥️ 控制台：发送指令
- 📁 文件管理：浏览、读取、写入、上传、重命名、删除、新建文件夹、一次性下载链接
- 💾 备份：查看、创建、恢复、删除；批量概览与批量创建
- 👥 玩家在线：从控制台日志解析进出（兼容原版/Paper/Spigot/Forge/Fabric 中英文格式）
- 🚨 崩服检测与告警：状态级健康检查 + 日志级崩溃信号（OOM/JVM/异常退出/致命错误），可配 webhook/邮件通知、冷却去重、连续确认防误报，`~/.pteropilot/alerts.json` 按服务器调灵敏度
- ⚡ 批量操作：一次看全部状态摘要，批量开/停/重启，批量备份概览/创建
- 🔒 安全：只读模式 + 高危操作二次确认，API Key 永不离开本机

## 安装

需要 Python 3.12+。用 [`uv`](https://docs.astral.sh/uv/) 一行跑起来：

```bash
uvx pteropilot
```

## 配置

在你的 MCP 客户端（Claude Desktop、Cursor 等）配置文件中加入：

```json
{
  "mcpServers": {
    "pteropilot": {
      "command": "uvx",
      "args": ["pteropilot"],
      "env": {
        "PANEL_URL": "https://panel.example.com",
        "PANEL_API_KEY": "ptlc_xxxxxxxxxxxxxxxxxxxx",
        "PANEL_READONLY": "0"
      }
    }
  }
}
```

| 环境变量 | 说明 |
|---|---|
| `PANEL_URL` | 你的面板地址 |
| `PANEL_API_KEY` | 面板 API Key（面板 → Account → API Credentials） |
| `PANEL_READONLY` | 设为 `1` 进入只读模式：禁止电源、写入、删除等操作。先试用、还不信任 AI 时建议开启 |

配置好后，直接对 AI 说人话即可，例如：

- 「列出我所有服务器和状态」
- 「smp 服最近一次备份是什么时候？没有就现在建一个」
- 「往 creative 服上传这个插件并重启」

## 崩服告警（webhook / 邮件）

`pteropilot-alerts` 命令以只读方式扫描你的服务器，当服务器处于异常态（离线 / 错误 / 挂起）或最近控制台日志出现崩溃信号时，推一条通知到钉钉 / 企业微信机器人 webhook，或发一封邮件。适合配 cron 定时跑：

```bash
# 安装
uv tool install pteropilot

# 试跑一次
PANEL_URL=https://panel.example.com PANEL_API_KEY=ptlc_xxx \
  pteropilot-alerts --webhook "https://oapi.dingtalk.com/robot/send?access_token=***"

# 每 5 分钟跑一次
*/5 * * * * PANEL_URL=... PANEL_API_KEY=... pteropilot-alerts \
  --webhook "https://oapi.dingtalk.com/robot/send?access_token=***" >> /var/log/pteropilot-alerts.log 2>&1
```

可选参数：`--min-level error`（只报错误态）、`--token ***`（需要 URL key 的企业微信机器人）、`--timeout <秒>`、`--no-log-signals`（只看状态、不读日志，更快）、`--cooldown <分钟>`（去重：同一服务器同一状态在冷却期内不重复通知；状态一变就立即告警——防止 cron 每几分钟跑一次时的告警风暴）、`--state-file <路径>`（冷却/确认状态持久化位置，默认 `~/.pteropilot/alerts_state.json`）、`--confirm-count <n>`（连续 N 次检测到异常才通知——过滤瞬时抖动；恢复健康归零计数）、`--config <路径>`（按服务器调灵敏度的配置文件，默认 `~/.pteropilot/alerts.json`）。

邮件通道（第二通知渠道，可与 webhook 同时用）：

```bash
pteropilot-alerts \
  --smtp-host smtp.example.com --smtp-user you@x.com --smtp-pass <授权码> \
  --mail-to ops@x.com            # --smtp-port 587 用 STARTTLS（默认 465 SSL）
  # --mail-from alerts@x.com     # 可选发件人（默认同 --smtp-user）
```

每个参数都有对应环境变量回退，便于把敏感信息移出命令行：

| 环境变量 | 对应参数 | 默认值 |
|---|---|---|
| `WEBHOOK_URL` | `--webhook` | （空） |
| `SMTP_HOST` | `--smtp-host` | （空） |
| `SMTP_PORT` | `--smtp-port` | `465` |
| `SMTP_USER` | `--smtp-user` | （空） |
| `SMTP_PASS` | `--smtp-pass` | （空） |
| `MAIL_TO` | `--mail-to` | （空） |
| `MAIL_FROM` | `--mail-from` | （空） |
| `CONFIRM_COUNT` | `--confirm-count` | `1` |

按服务器调灵敏度（可选）：把 JSON 放到 `~/.pteropilot/alerts.json`（或传 `--config <路径>`）。优先级：服务器条目 > `default` > 命令行参数。

```json
{
  "default": { "confirm_count": 2, "cooldown_minutes": 15 },
  "servers": {
    "smp":      { "confirm_count": 3, "cooldown_minutes": 60 },
    "creative": { "min_level": "error", "email": false },
    "auth":     { "webhook": false, "email": true, "mail_to": "oncall@x.com" },
    "preview":  { "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=***" }
  }
}
```

只发 GET 请求；绝不触发电源 / 指令 / 写操作。

## 从源码运行（开发者）

```bash
git clone https://github.com/telson-dev/pteropilot.git
cd pteropilot
uv sync
uv run pteropilot
```

## 兼容性

同时支持 **Pterodactyl v1** 与 **Pelican**。所用 client / application API 端点及 payload 形状均已对照 Pelican 面板源码（routes 与 controllers）核实，一个 MCP server 两个面板通用。

## 状态与路线图

当前发布版：**v0.2.5**（见 [CHANGELOG](CHANGELOG.md)）。下一步计划见 [ROADMAP](ROADMAP.md)；完整安装指南、两款面板配置示例与 FAQ 见 [docs/](docs/README.md)。

## 安全说明

- API Key 只保存在你本机的客户端配置中，仅用于直接请求你的面板，不经过任何第三方服务器
- 不放心就开 `PANEL_READONLY=1`，AI 只能看不能动
- 建议给 AI 使用权限最小化的 API Key

## 文档

[快速上手、Pterodactyl/Pelican 配置示例、FAQ](docs/README.md)

## 许可

MIT

---

_a [telson](https://telson.dev) project_
