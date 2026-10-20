# PteroPilot

**English** | [中文](README.zh-CN.md)

![PteroPilot](assets/pteropilot-icon.png)

**Manage your game servers with plain language.**
PteroPilot is an open-source MCP server that lets AI assistants like Claude and Cursor directly manage game servers on [Pterodactyl](https://pterodactyl.io) / [Pelican](https://pelican.dev) panels.

> "Check how the survival server is doing, restart it if it's lagging, and see whether the latest backup succeeded."

No panel login, no digging through logs — just ask.

## What it does

- 📊 List servers, live status, and resource usage
- ⚡ Power controls: start / stop / restart / kill
- 🖥️ Console: send commands
- 📁 Files: browse, read, write, upload, rename, delete, create folders, one-time download links
- 💾 Backups: list, create, restore, delete
- 🔒 Safety: read-only mode and explicit confirmation for dangerous actions; your API key never leaves your machine

## Install

Requires Python 3.12+. Run in one line with [`uv`](https://docs.astral.sh/uv/):

```bash
uvx pteropilot
```

## Configure

Add this to your MCP client config (Claude Desktop, Cursor, etc.):

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

| Variable | Description |
|---|---|
| `PANEL_URL` | Your panel URL |
| `PANEL_API_KEY` | Panel API key (Panel → Account → API Credentials) |
| `PANEL_READONLY` | Set to `1` for read-only mode: blocks power, write, and delete actions. Recommended while trying it out |

Then just talk to your AI naturally, for example:

- "List all my servers and their status"
- "When was the latest backup on smp? Create one if missing"
- "Upload this plugin to the creative server and restart it"

## Run from source

```bash
git clone https://github.com/telson-dev/pteropilot.git
cd pteropilot
uv sync
uv run pteropilot
```

## Compatibility

Targets both **Pterodactyl v1** and **Pelican**. Every client/application endpoint and payload shape used here has been verified against the Pelican panel source (routes and controllers), so one MCP server drives either panel.

## Status & roadmap

Current release: **v0.2.1** (see [CHANGELOG](CHANGELOG.md)). Full setup guide, panel config examples, and troubleshooting in [docs/](docs/README.md).

## Security

- Your API key lives only in your local client config and talks directly to your panel — it never passes through any third-party server
- Set `PANEL_READONLY=1` if you want the AI to be look-but-don't-touch
- Prefer a least-privilege API key for AI use

## Docs

[Quick start, Pterodactyl/Pelican config examples, and FAQ](docs/README.md)

## License

MIT

---

_a [telson](https://telson.dev) project_
