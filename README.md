# PteroPilot

[![CI](https://github.com/telson-dev/pteropilot/actions/workflows/ci.yml/badge.svg)](https://github.com/telson-dev/pteropilot/actions/workflows/ci.yml)
**English** | [中文](README.zh-CN.md)

![PteroPilot](assets/pteropilot-icon.png)

**Manage your game servers with plain language.**
PteroPilot is an open-source MCP server that lets AI assistants like Claude and Cursor directly manage game servers on [Pterodactyl](https://pterodactyl.io) / [Pelican](https://pelican.dev) panels.

> "Check how the survival server is doing, restart it if it's lagging, and see whether the latest backup succeeded."

No panel login, no digging through logs — just ask.

## What it does

- 📊 List servers, live status, and resource usage
- 🌐 Network addresses: IP/port allocations with the primary address marked ("what's my server address?")
- 🗄️ Databases: the panel-assigned database host/db/user for plugins that need MySQL (LuckPerms, CoreProtect…)
- 📜 Audit log: who restarted / deleted / backed up what, and when (from panel activity events)
- ⏰ Schedules: panel-configured cron tasks (expression, enabled, last/next run)
- 📊 Player activity analysis: who plays most, peak hours, who is still online (from logs)
- 👥 Sub-user permissions: who else can manage this server and what they were granted
- 🗂️ Mounts: which extra storage this server has, and whether it is read-only
- ⚡ Power controls: start / stop / restart / kill
- 🖥️ Console: send commands
- 📁 Files: browse, read, write, upload, rename, delete, create folders, one-time download links
- 💾 Backups: list, create, restore, delete; batch overview and batch-create across servers
- 👥 Who is online: parse join/leave lines from the console (Minecraft/Paper/Spigot/Forge/Fabric, EN & CN)
- 🚨 Crash detection & alerts: state-level health checks, CPU/memory thresholds, + log-level crash signals (OOM/JVM/exit/fatal), optional webhook/email alerts with cooldown dedup, confirm-count anti-flap, and per-server tuning via `~/.pteropilot/alerts.json`
- ⚡ Batch ops: one-shot status summary, batch start/stop/restart, batch backup overview/create
- 🔒 Safety: read-only mode and explicit confirmation for dangerous actions; your API key never leaves your machine

Batch tools fan out across servers, but **never all at once**: they run at most 8
concurrent requests by default, so a 40-server fleet does not open 40 connections
into one panel and get rate-limited. Tune it with `PTEROPILOT_CONCURRENCY`
(clamped to 1–32):

```bash
PTEROPILOT_CONCURRENCY=4  pteropilot --status   # gentler on a small panel
PTEROPILOT_CONCURRENCY=16 pteropilot --status   # faster when the panel is beefy
```

Transient failures are retried automatically, **but only for read-only calls**.
A `429`, `502`, `503`, `504`, or a dropped connection on a GET is retried up to
two more times with exponential backoff and jitter, honouring `Retry-After`
(capped at 5s). Writes are never retried: re-sending a rate-limited `stop`, or a
`delete` whose response was lost, is worse than showing you the error.

```bash
PTEROPILOT_RETRIES=0 pteropilot --status   # disable retries entirely
```

## Install

Requires Python 3.12+. Run in one line with [`uv`](https://docs.astral.sh/uv/):

```bash
uvx pteropilot
```

### Upgrade to the latest

`uvx` reuses its cache — pull the latest on purpose:

```bash
uvx --refresh pteropilot      # or: uvx pteropilot@latest
pip install -U pteropilot     # if you installed via pip
```

**PyPI can lag behind GitHub** while a release is pending. Check what you got:

```bash
pteropilot --version     # fewer tools than the README lists = old build
```

If PyPI is not updated yet, install the `.whl` from
[GitHub Releases](https://github.com/telson-dev/pteropilot/releases):

```bash
pip install pteropilot-<version>-py3-none-any.whl
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
        "PANEL_URL": "https://panel.example.com",   # panel root URL, no /api suffix
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
| `PANEL_API_KEY` | Panel **client** API key (`ptlc_` on Pterodactyl / `pacc_` on Pelican). Powers the core tools |
| `PANEL_APP_KEY` | Optional **application** API key (`ptla_` / `papp_`). Needed for `list_nodes` / `list_all_servers` / `list_users`; without it those tools fail on Pterodactyl (403) |
| `PANEL_READONLY` | Set to `1` for read-only mode: blocks power, write, and delete actions. Recommended while trying it out |

Panel compatibility details: see [docs/compatibility.md](docs/compatibility.md).

Then just talk to your AI naturally, for example:

- "List all my servers and their status"
- "When was the latest backup on smp? Create one if missing"
- "Upload this plugin to the creative server and restart it"

## Cannot reach your panel? Run the doctor

```bash
pteropilot --doctor              # checks Python, config, key prefix, connectivity, alert channels
pteropilot --doctor --no-network # config only, no network calls
```

The output never contains secrets (only key prefixes), so you can paste it
as-is when asking for help.

Since v0.2.34 the doctor also compares your build against the latest GitHub
release and prints the exact upgrade command when you are behind:

```
版本：v0.2.20 → 最新 v0.2.34（落后）
  · 装的是旧版：最新 v0.2.34，当前 v0.2.20。升级命令：
      uv tool install --force https://github.com/telson-dev/pteropilot/releases/latest/download/pteropilot-0.2.33-py3-none-any.whl
```

This matters because PyPI can lag far behind (it sat at 0.2.0 for weeks), and an
old build looks like "missing tools" rather than "wrong version". The check is
informational only — a failed lookup prints `查不到最新版` and never fails the run.

## Broadcast one command to every server

```bash
pteropilot --broadcast "say Restarting in 5 minutes"              # running servers only
pteropilot --broadcast "whitelist add Steve"                     # safe commands need no confirm
pteropilot --broadcast "stop" --confirm                          # dangerous ones do
pteropilot --broadcast "say hi" --servers smp,creative           # pick servers
pteropilot --broadcast "say hi" --json                           # machine-readable
```

Useful in cron/systemd timers: no MCP client needed, one command, then exit.
Dangerous commands (`stop` / `kill` / `op` / `ban` / `whitelist off` / ...) are
refused without `--confirm`. Exit codes: `0` all sent, `1` some failed,
`2` bad usage, `3` refused (read-only / missing confirm).
`--json` prints `{status, command, scope, total, ok, failed, results[]}`;
## Power signals from the shell

```bash
pteropilot --power start   --servers smp,creative      # start is safe
pteropilot --power restart --servers smp --confirm     # stop/restart/kill need --confirm
pteropilot --power stop    --servers "*" --confirm     # "*" = every server
pteropilot --power restart --servers smp --confirm --json
pteropilot --power restart --servers smp --confirm --at 03:00   # wait until 03:00
pteropilot --broadcast "say brb" --in 5m                        # wait 5 minutes
```

## Look at your servers without an AI client

```bash
pteropilot --status              # every server: state, health, CPU, memory, disk
pteropilot --status smp          # one server, plus online players (from console log)
pteropilot --status --json       # machine-readable
```

A single failing server is listed as `⚠` and does not hide the rest.

Exit codes (so a cron job can tell apart "fix your config" from "a server died"):

| Code | Meaning |
|---|---|
| `0` | All healthy |
| `1` | At least one server is in an abnormal state |
| `2` | Bad argument |
| `3` | Missing or invalid config (`PANEL_URL` / `PANEL_API_KEY`) |
| `4` | Panel unreachable, credentials rejected, or some servers could not be queried |

Previously `1` meant both "config is wrong" and "a server is down".

Add `--watch` to keep it running and only reprint when something actually changes
(CPU and memory wobble every second, so they are deliberately excluded from the
change detection — otherwise it would redraw forever and you would still miss the
moment a server died):

```bash
pteropilot --status --watch                  # every 30s until Ctrl+C
pteropilot --status --watch --interval 10    # 5–3600 seconds
pteropilot --status --watch --json           # one JSON object per line, on change only
```

Watch keeps state in memory between rounds, so it can tell you **how long a
server has been in its current state** — the difference between "just died 30s
ago" and "been down for two hours" matters:

```
smp        STOPPED  🔴 stopped   12.3%   800M/2048M   3G/10G   1时20分
lobby      RUNNING  🟢 running   12.3%   800M/2048M   3G/10G        -
```

That column only appears in watch mode (a one-shot query has no previous round
to compare against), and healthy servers show `-` — it answers "how long has
this been broken", not "how long has this been up". Plain-output heartbeats also
mention the longest-running problem:

```
[09:41:02] 无变化（1/2 正常，最长异常 smp 已 1时20分）
```
```

Backups are the thing you only miss after a crash, so `--backups` adds a "newest
backup" column and calls out anything stale (default threshold 7 days):

```bash
pteropilot --status --backups                 # newest backup age per server
pteropilot --status --backups --backup-days 30
```

When that warning fires, the next question is "which backup can I restore from?"
— `--backups` lists them, newest first:

```bash
pteropilot --backups smp                 # newest 20: created / size / state / locked
pteropilot --backups smp --limit 50 --json
pteropilot --backups smp --create       # take one right now
pteropilot --backups smp --create --wait # ... and wait, then print the link
```

`--create` is the piece that used to require going through an AI client. A single
server needs no confirmation (same as the `create_backup` MCP tool); `"*"` or
`a,b` **requires `--confirm`**, because starting backups on every server at once
can exhaust storage — and a full disk is exactly what stops saves from being
written. `PANEL_READONLY=1` refuses it.

`--wait` polls until the backup finishes (default 600s, `--wait-timeout` to
change) and then fetches its one-time download link, so a script can back up and
grab the file in one command. A backup that never completes exits 4; failing to
fetch the link afterwards does **not** turn a successful backup into a failure.

To actually restore one, ask for its one-time download link. You can name the
backup instead of typing a 36-character UUID (a unique prefix is enough):

```bash
pteropilot --backup-url smp weekly-1     # prints a one-time URL
pteropilot --backup-url smp weekly-1 --json
```

If the name matches more than one backup it lists the candidates and exits 2 —
restoring the wrong backup is not undoable, so it never guesses.

The scheduled alerter can watch this for you, so you find out without looking:

```bash
pteropilot-alerts --webhook https://... --backup-days 7
```

A server whose newest successful backup is older than the threshold (or that has
never had one) is reported with a synthesized `stale_backup` state. If the backup
API itself fails, **no alert is raised** — a false "never backed up" would train
you to ignore the channel.

`--disk` watches disk usage the same way `--cpu` and `--memory` watch load: a
server whose disk is nearly full cannot write its world saves, and that failure
is silent until something breaks. Servers without a disk quota on the panel are
skipped rather than scored against zero.

`--doctor` groups its checks (panel config / runtime knobs / local state /
network) and ends with a count of outstanding problems, so the output is
readable when pasted into a bug report.

`--doctor` also reports how old the newest local player sample is. If you wired
`--players --record` into cron, that line is the only place you would notice the
cron job silently stopped — otherwise your trends just quietly go stale.

`--doctor` also validates the tuning knobs. They all fall back silently when
misconfigured, and `PANEL_READONLY` is the dangerous one: only `1`/`true`/`yes`/`on`
enable it, so `PANEL_READONLY=enabled` leaves writes **unlocked** while looking
like protection.

A misspelled key in this file is silently ignored, which means the alert never
fires and nothing tells you why. `pteropilot --doctor` validates the file and
suggests the intended key ("did you mean `cpu_threshold`?"), so
"configured but never fires" is diagnosable.

All four thresholds (`cpu_threshold`, `memory_threshold`, `disk_threshold`,
`backup_days`) can be overridden per server in `~/.pteropilot/alerts.json`,
because one global number cannot fit both a heavy modpack and a tiny lobby:

```json
{
  "default": { "cpu_threshold": 85, "memory_threshold": 90, "disk_threshold": 85, "backup_days": 7 },
  "servers": {
    "heavy-modpack": { "cpu_threshold": 97, "memory_threshold": 98 },
    "tiny-lobby":    { "cpu_threshold": 60, "backup_days": 2 }
  }
}
```

Precedence is server entry > `default` > command-line flag.

Failed backups are ignored when computing the age — a backup that cannot be
restored is not a backup. "Never" (no successful backup) and "unknown" (the backup
API call failed) are reported differently on purpose.

When a server crashes or a command does nothing, the next question is always "what
does the console say". `--log` prints that tail straight into your terminal:

```bash
pteropilot --log smp                       # last 80 lines
pteropilot --log smp --lines 300           # deeper (1–2000)
pteropilot --log smp --grep error          # only matching lines, case-insensitive
pteropilot --log smp --grep Started --json # machine-readable
pteropilot --log smp --watch               # follow like tail -f: only new lines

pteropilot --log "*"                       # every accessible server, at once
pteropilot --log smp,creative              # just these two
pteropilot --log "*" --grep error --json   # {servers: [...], grep: ...}
```

`--watch` follows the console the way `tail -f` does: it polls every
`--interval` seconds (default 5, clamped 2–3600) and prints only the lines that
are genuinely new. The panel returns "the last N lines" rather than a delta, so
the new part is found by aligning the previous buffer with the current one; when
they share no overlap at all it says so explicitly ("console changed wholesale,
possibly restarted") instead of pretending everything is new. `--json` emits one
object per round, and only when something arrived.

Multi-server mode fetches concurrently (capped, same as the other batch tools) and
a single failing server is reported inline instead of hiding the rest. A server
with no matching lines says so explicitly rather than being silently skipped. In
`--json` mode the top level is `{"servers": [...], "grep": ...}` — different from
the single-server `{"server_id": ...}` shape, so scripts can tell them apart.

ANSI colour codes and carriage returns are stripped, so the output stays readable
when piped or pasted. Exit code is `1` when there is nothing to show (empty console,
or nothing matched `--grep`) — handy for `until pteropilot --log smp --grep Started`;
`2` means the argument itself was wrong.

Player activity is logged locally every time `list_players` runs (one sample per
call, in `~/.pteropilot/playerlog/`). If you only use the CLI, that history would
never accumulate — `--record` takes a sample right now, so you can build it from
cron without opening an AI client:

```bash
pteropilot --players --record          # sample every accessible server
pteropilot --players smp --record      # just this one
*/30 * * * * pteropilot --players --record    # half-hourly history
```

`--players` reads those files directly, so it needs **no panel config and no
network** — it still works when the panel is down:

```bash
pteropilot --players                    # which servers have local samples
pteropilot --players smp                # 7-day trend: minutes online, busiest hours
pteropilot --players smp --days 30      # wider window (1–365)
pteropilot --players smp --top 20       # longer leaderboard (1–100)
pteropilot --players smp --json         # machine-readable
```

Exit code `1` when there are no samples yet (so cron can tell "not enough data"
from "broken"), `2` on a bad argument. The numbers are inferred from the console
log, not authoritative playtime — sample more often for a closer estimate.

`--in` / `--at` make the process wait before acting — handy when a cron entry
cannot express "at 03:00 only if it is currently running". Wait is capped at
24 hours (anything longer belongs in cron/systemd); Ctrl+C during the wait
cancels and exits with code `4`, sending nothing.

Same logic as the `batch_power` tool. `--servers` is required (a power signal is a
write; it will not guess a scope). Exit codes match `--broadcast`.
#
refusals carry `reason` (`readonly` / `need_confirm` / `empty_command` / `no_running` / `no_targets`).


## Check your version first

```bash
pteropilot --version
# pteropilot v0.2.34 (45 MCP tools)
```

**If the tool count looks low you are on an old build.** PyPI may lag behind
GitHub Releases; install the latest `.whl` from
[GitHub Releases](https://github.com/telson-dev/pteropilot/releases).

## Crash alerts (webhook)

The `pteropilot-alerts` command scans your servers read-only and pushes a
notification to a DingTalk / WeCom (WeChat Work) group-bot webhook when a server
is in an abnormal state (offline / error / suspended) or its recent console log
shows crash signals. Perfect for cron:

```bash
# install
uv tool install pteropilot

# test once
PANEL_URL=https://panel.example.com PANEL_API_KEY=ptlc_xxx \
  pteropilot-alerts --webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx"

# run every 5 minutes
*/5 * * * * PANEL_URL=... PANEL_API_KEY=... pteropilot-alerts \
  --webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx" >> /var/log/pteropilot-alerts.log 2>&1

# verify the notification channel (no server reads, no state writes)
pteropilot-alerts --webhook "..." --test

# stuck in cooldown / changed sensitivity: clear state and start over
pteropilot-alerts --reset

# exit codes: 0=ok; 1=config error; 3=alert fired but delivery failed (nobody was notified)
```

Options: **On Windows** (local servers / Windows VPS): no cron needed — install a scheduled task
with one command:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_alerts_task.ps1 `
    -Webhook "https://oapi.dingtalk.com/robot/send?access_token=***"

# every 15 min, only error states / e-mail channel / status / uninstall
... -IntervalMinutes 15 -MinLevel error
... -SmtpHost smtp.qq.com -SmtpUser you@qq.com -SmtpPass <app password> -MailTo ops@x.com
... -Status
... -Remove
```

It generates a wrapper script (panel credentials written in, so it works in the
background), logs to `%USERPROFILE%\.pteropilot\alerts.log`, and supports `-DryRun`.

`--min-level error` (only error state), `--token <key>` (WeCom bots
that need a URL key), `--timeout <sec>`, `--no-log-signals` (state-only, faster),
`--cooldown <minutes>` (dedupe: same server + same state is not re-notified
within the cooldown; a state change always alerts immediately — prevents alert
storms when cron runs every few minutes),
`--state-file <path>` (persist cooldown/confirm state to a custom path; default
`~/.pteropilot/alerts_state.json`),
`--confirm-count <n>` (require N consecutive abnormal detections before
notifying — filters transient flaps; recovery resets the counter),
`--config <path>` (per-server tuning file; default `~/.pteropilot/alerts.json`),
`--dry-run` (rehearsal: print which servers *would* be notified and on which
channels, without sending anything or touching the state file — handy when
tuning thresholds or before turning cron on),
`--json` (machine-readable output: result with `errors` / `channels` / `planned`
as JSON, for cron or monitoring scripts).
`--test` (channel self-check: sends one test message to verify the webhook /
e-mail setup — no server data, no state writes; exits 1 with common causes on
failure). Run it before enabling the schedule.

Email channel (second notifier, can be combined with the webhook):

```bash
pteropilot-alerts \
  --smtp-host smtp.example.com --smtp-user you@x.com --smtp-pass <auth-code> \
  --mail-to ops@x.com            # --smtp-port 587 for STARTTLS (default 465 SSL)
  # --mail-from alerts@x.com     # optional sender (defaults to --smtp-user)
```

Every option has an environment-variable fallback, so you can keep secrets out
of the command line and cron entries:

| Env var | Maps to | Default |
|---|---|---|
| `WEBHOOK_URL` | `--webhook` | *(empty)* |
| `SMTP_HOST` | `--smtp-host` | *(empty)* |
| `SMTP_PORT` | `--smtp-port` | `465` |
| `SMTP_USER` | `--smtp-user` | *(empty)* |
| `SMTP_PASS` | `--smtp-pass` | *(empty)* |
| `MAIL_TO` | `--mail-to` | *(empty)* |
| `MAIL_FROM` | `--mail-from` | *(empty)* |
| `CONFIRM_COUNT` | `--confirm-count` | `1` |

Per-server tuning (optional): drop a JSON at `~/.pteropilot/alerts.json` (or pass
`--config <path>`). Precedence: server entry > `default` > CLI options.

```json
{
  "default": { "confirm_count": 2, "cooldown_minutes": 15 },
  "servers": {
    "smp":      { "confirm_count": 3, "cooldown_minutes": 60 },
    "creative": { "min_level": "error", "email": false },
    "auth":     { "webhook": false, "email": true, "mail_to": "oncall@x.com" },
    "preview":  { "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=bbb" }
  }
}
```

Per-server keys: `confirm_count`, `cooldown_minutes`, `min_level`,
`webhook` (bool), `email` (bool), `webhook_url` (route to another bot),
`mail_to` (route email to another recipient).

Only GET requests are issued; no power/command/write actions are ever triggered.

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

Current release: **v0.2.34** (see [CHANGELOG](CHANGELOG.md)). See [ROADMAP](ROADMAP.md) for what's next. Full setup guide, panel config examples, and troubleshooting in [docs/](docs/README.md).

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


## Alert on load, not just on death

`pteropilot-alerts` originally only fired when a server was offline/error/suspended.
A server that is *running* but pegged at 99% CPU used to look perfectly healthy:

```bash
pteropilot-alerts --webhook "..." --cpu 85 --memory 90
```

CPU is an absolute percentage; memory is a **usage ratio** (`memory_bytes / memory_limit`).
Servers without a memory limit are skipped rather than scored against zero.
Hitting the threshold exactly counts as over. Default `0` disables both, so the
behaviour is unchanged unless you ask for it.
