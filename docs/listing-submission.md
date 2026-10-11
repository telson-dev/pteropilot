# PteroPilot MCP 目录上架材料

> 生成时间：2026-10-01。上架 3 个平台 = 实际 2 个动作（mcp.so 开 issue + awesome-mcp-servers 提 PR；Glama 与 awesome 同数据源自动同步）。

## 一、awesome-mcp-servers（= Glama，★95.7k，最大流量池）

### 定位
- 分类：**🎮 Gaming**（正文 `### 🎮 <a name="gaming"></a>Gaming`，约 2458 行）
- 插入位置：`taygunsavas/patina-unity-mcp` 之后、`truetick-gg/mcp` 之前（字母序，`telson` < `truetick`）

### 要加的一行（直接粘）
```markdown
- [telson-dev/pteropilot](https://github.com/telson-dev/pteropilot) [![telson-dev/pteropilot MCP server](https://glama.ai/mcp/servers/telson-dev/pteropilot/badges/score.svg)](https://glama.ai/mcp/servers/telson-dev/pteropilot) 🐍 ☁️ 🏠 🍎 🪟 🐧 - Manage Pterodactyl/Pelican game servers with plain language: list servers, power control, console commands, file management incl. upload/rename, and backups — with read-only mode and confirm guardrails.
```

### 图例说明（对齐现有格式）
- 🐍 = Python 代码库
- ☁️ ☁️ = Cloud（调远程 panel API）+ 🏠 = Local（自托管安装）
- 🍎 🪟 🐧 = 三平台
- 描述一句话，说明「做什么 + 关键特性」

### 操作步骤
1. Fork `punkpeye/awesome-mcp-servers`
2. 建分支（如 `add-pteropilot`）
3. 编辑 `README.md`，在 Gaming 分类插入上面那行
4. commit + push 到你的 fork
5. 提 PR，**标题末尾加 `🤖🤖🤖`**（自动化 agent 快车道，加速合并）

---

## 二、mcp.so（社区目录）

### 方式
- 开 GitHub issue（点网站 `Submit` 或直接进其 issue 页）
- 仓库：mcp.so 站点说明「Submit button in nav / GitHub issues page」

### issue 正文模板
```markdown
## Server: PteroPilot

**Name:** PteroPilot
**Repo:** https://github.com/telson-dev/pteropilot
**Install:** `uvx pteropilot`

**Description:**
An MCP server that lets AI assistants (Claude, Cursor, etc.) manage game
servers on Pterodactyl / Pelican panels with plain language — list servers,
live status & resource usage, power control (start/stop/restart/kill),
console commands, file management (browse/read/write/upload/rename/delete),
and backups (create/list/restore/delete).

**Features:**
- Dual-panel: supports both Pterodactyl and Pelican (same origin API)
- 18 MCP tools covering servers, power, console, files, backups
- Safety: PANEL_READONLY=1 read-only mode + confirm=True for dangerous ops
- Your API key never leaves your machine
- Python 3.12+, install in one line via uvx

**Connection:**
```json
{
  "mcpServers": {
    "pteropilot": {
      "command": "uvx",
      "args": ["pteropilot"],
      "env": {
        "PANEL_URL": "https://panel.example.com",
        "PANEL_API_KEY": "ptlc_xxx",
        "PANEL_READONLY": "0"
      }
    }
  }
}
```

**License:** MIT
```

---

## 三、Glama 独立站
无需单独操作。awesome-mcp-servers PR 合并后，Glama 目录（glama.ai/mcp/servers）自动同步。