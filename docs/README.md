# PteroPilot 文档

> 快速上手 + 两款面板（Pterodactyl / Pelican）配置示例 + 故障排查 FAQ。

目录：

- [快速上手](#快速上手)
- [环境变量参考](#环境变量参考)
- [Pterodactyl 面板配置示例](#pterodactyl-面板配置示例)
- [Pelican 面板配置示例](#pelican-面板配置示例)
- [工具一览](#工具一览)
- [安全模型：只读模式与二次确认](#安全模型只读模式与二次确认)
- [故障排查 FAQ](#故障排查-faq)

---

## 快速上手

**前置条件**：Python 3.12+，以及一个可访问的 Pterodactyl / Pelican 面板账号和 API Key。

1. **安装**（用 [`uv`](https://docs.astral.sh/uv/) 一行跑起，无需手动克隆）：

   ```bash
   uvx pteropilot
   ```

   也可以从源码运行（开发者）：

   ```bash
   git clone https://github.com/telson-dev/pteropilot.git
   cd pteropilot
   uv sync
   uv run pteropilot
   ```

2. **拿 API Key**：面板后台 → 账户（Account）→ API 凭证（API Credentials），新建一个 Key 并复制。

   > 建议专门为 AI 使用创建一个**权限最小化**的 Key：只授予你希望 AI 能操作的那几台服务器、去掉删除类权限。

3. **配置 MCP 客户端**：在 Claude Desktop / Cursor 等客户端的 MCP 配置里加一个 server（见下方示例）。

4. **先开只读模式试水**：`PANEL_READONLY=1`，让 AI 只能看不能动；确认行为符合预期后再放开写入。

5. **开始对话**：

   - "列出我所有服务器和状态"
   - "smp 服最近一次备份是什么时候？没有就现在建一个"
   - "往 creative 服上传这个插件并重启"

---

## 环境变量参考

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `PANEL_URL` | ✅ | 面板地址，如 `https://panel.example.com`。从浏览器地址栏复制，**不要带尾随斜杠**。 |
| `PANEL_API_KEY` | ✅ | 面板 API Key（面板 → 账户 → API 凭证）。 |
| `PANEL_READONLY` | 否 | 设为 `1` 进入只读模式，禁止电源、写入、删除等操作。默认 `0`。`1/true/yes/on`（大小写、空格不敏感）均生效。 |

---

## Pterodactyl 面板配置示例

### Claude Desktop

编辑 `claude_desktop_config.json`：

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

### Cursor

在 Cursor 的 MCP 配置文件（`.cursor/mcp.json`，项目级）中：

```json
{
  "mcpServers": {
    "pteropilot": {
      "command": "uvx",
      "args": ["pteropilot"],
      "env": {
        "PANEL_URL": "https://panel.example.com",
        "PANEL_API_KEY": "ptlc_xxxxxxxxxxxxxxxxxxxx",
        "PANEL_READONLY": "1"
      }
    }
  }
}
```

---

## Pelican 面板配置示例

配置方式与 Pterodactyl 完全一致，唯一不同是 `PANEL_URL` 指向你的 Pelican 面板地址。PteroPilot 在底层同时兼容两款面板，一个 MCP server 通用。

### Claude Desktop

```json
{
  "mcpServers": {
    "pteropilot": {
      "command": "uvx",
      "args": ["pteropilot"],
      "env": {
        "PANEL_URL": "https://pelican.example.com",
        "PANEL_API_KEY": "ptlc_xxxxxxxxxxxxxxxxxxxx",
        "PANEL_READONLY": "0"
      }
    }
  }
}
```

> Pelican 是 Pterodactyl 的社区分叉，API 端点与 payload 形状大体同源。PteroPilot 所用全部端点均已对照 Pelican 面板源码（routes 与 controllers）核实，确保一个 server 两个面板通用。

---

## 工具一览

| 工具 | 类型 | 说明 | 需 confirm |
|---|---|---|---|
| `list_my_servers` | 只读 | 列出当前 Key 可访问的服务器 | — |
| `list_all_servers` | 只读【管理员】 | 列出面板全部服务器 | — |
| `list_nodes` | 只读【管理员】 | 列出所有节点 | — |
| `get_server_status` | 只读 | 单服实时状态与资源占用 | — |
| `list_files` | 只读 | 列出目录内容 | — |
| `read_file` | 只读 | 读取文本文件 | — |
| `download_file` | 只读 | 获取一次性下载链接（约 15 分钟） | — |
| `list_backups` | 只读 | 列出备份 | — |
| `get_console_log` | 只读 | 读取控制台最近日志 | — |
| `power_signal` | 写入 | start / stop / restart / kill | stop/restart/kill ✅ |
| `send_command` | 写入 | 发控制台指令 | — |
| `write_file` | 写入 | 写入文本文件（覆盖需确认） | 覆盖 ✅ |
| `delete_files` | 写入 | 删除文件/目录 | ✅ |
| `create_folder` | 写入 | 新建文件夹 | — |
| `rename_file` | 写入 | 重命名/移动（覆盖同名需确认） | 覆盖 ✅ |
| `upload_file` | 写入 | 上传文件（base64 内容；覆盖同名需确认） | 覆盖 ✅ |
| `create_backup` | 写入 | 创建备份 | — |
| `restore_backup` | 写入 | 恢复备份（回滚文件） | ✅ |
| `delete_backup` | 写入 | 删除备份 | ✅ |

---

## 安全模型：只读模式与二次确认

PteroPilot 有两道防线：

1. **只读模式**（`PANEL_READONLY=1`）：全局禁止所有电源、写入、删除、恢复类操作。此类调用会直接返回拒绝提示，不会发出任何请求。

2. **二次确认**（`confirm=True`）：对高危操作——`stop` / `restart` / `kill` 电源操作、覆盖文件的写入 / 上传 / 重命名、删除文件/目录、恢复 / 删除备份——工具默认拦截并提示，需显式带 `confirm=True` 才会真正执行。`start` 免确认。

> 二次确认是**调用方（AI）通过参数声明**的约定，不是面板侧的硬性门禁；面板最终仍会以 API Key 的权限为准。建议配合**权限最小化的 Key**双保险。

API Key 只保存在你本机的客户端配置中，仅用于直接请求你的面板，不经过任何第三方服务器。

---

## 故障排查 FAQ

**Q1：启动报错「需要设置 PANEL_URL 和 PANEL_API_KEY 环境变量」？**

没有读到环境变量。检查客户端 MCP 配置里是否写了 `env` 段，且键名与上面一致（大写、下划线）。确认配置改动后需**重启客户端**让 MCP server 重新启动。

**Q2：`PANEL_URL` 要不要带尾随斜杠？**

不要。直接复制浏览器地址栏的地址即可，如 `https://panel.example.com`，结尾不加 `/`。

**Q3：API Key 生成后还是连不上（401 / 权限不足）？**

- 检查 Key 是否已复制完整（Pterodactyl/Pelican 的 Key 以 `ptlc_` 开头）。
- 确认这个 Key 拥有目标服务器的访问权限；「管理员」类工具（`list_all_servers` / `list_nodes`）需要应用级/管理员权限的 Key。
- 确认面板地址能从本机访问（非仅内网）。

**Q4：为什么操作被拦下了，提示「高危操作，已拦截」？**

这是二次确认在起作用。对高危操作，AI 需要显式带 `confirm=True` 重新调用。若你只是想「看看」，这正是期望的行为。

**Q5：为什么所有写入都返回「只读模式，已拒绝」？**

`PANEL_READONLY` 被设成了开启值（`1/true/yes/on`）。把它改回 `0` 或直接删掉该变量，重启客户端即可。

**Q6：只支持 Pterodactyl，不支持 Pelican？**

已支持两款。`PANEL_URL` 指向 Pelican 面板即可，其余配置与 Pterodactyl 完全一致。

**Q7：文件上传提示「content_b64 不是合法的 base64」？**

`upload_file` 的 `content_b64` 需要是文件内容的 base64 编码，不是原始字节，也不是带 `data:` 前缀的 data URL。先对内容做一次 base64 编码再传入。

**Q8：`get_server_status` 的状态显示不对？**

确保用的是 v0.2.0 及以后版本（早期版本有 `current_state` 读取位置错误，已在 v0.2.0 修复）。可 `uvx pteropilot` 直接拉最新版。

**Q9：能看到服务器但控制台命令没反应？**

发完指令后用 `get_console_log` 看回馈（v0.2.1 起提供）。若确无输出，检查该服务器是否在运行、以及 Key 是否有发送控制台指令的权限。