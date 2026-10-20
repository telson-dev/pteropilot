# Security Policy

## Reporting a Vulnerability

If you discover a security issue in PteroPilot, please **do not** open a public issue.

- Report it privately via GitHub's [**Report a vulnerability**](https://github.com/telson-dev/pteropilot/security/advisories/new) feature, or
- Email the maintainer (see the repository profile).

Please include steps to reproduce, impact, and any suggested fix. You should receive an acknowledgment within 72 hours.

## Safety Design

PteroPilot is built to operate against your own panel with minimal risk:

- **API key stays local.** Your key is read from the environment and sent only directly to your panel; it is never logged or transmitted to a third party.
- **Read-only mode.** Set `PANEL_READONLY=1` to block all power, write, delete, and restore actions.
- **Explicit confirmation.** High-risk actions (`restart`, `kill`, file overwrite/delete, backup restore/delete) require `confirm=True`.
- **Console commands.** `send_command` is blocked in read-only mode but is not confirmation-gated, because it is the core interactive path. Use `PANEL_READONLY=1` when the agent must never write.
- **Least privilege.** Prefer a scoped/limited panel API key dedicated to AI use.

## Supported Versions

While the project is pre-1.0, only the latest released version receives fixes.

## Security Audit — 2026-10-20

Scope: `src/pteropilot/` at commit `8cfcf07` (v0.2.x). Method: source review plus
repository-wide search for credential handling and guardrail paths.

### 1. API key exposure

| Vector | Result |
|---|---|
| Error messages | **Fixed.** `PanelError` text now passes through `redact()`, which strips `Bearer <token>` and `ptlc_/ptla_/peli_` keys before a panel error body or transport exception is surfaced. |
| Logs | No `logging` usage in `src/`; nothing writes request headers to disk. |
| Exception stacks | `httpx` failures are re-raised as `PanelError`; the original is chained with `from exc` for debuggability. Inner `httpx` messages do not carry headers, but user-facing output should use the `PanelError` message, not the full traceback. |
| Dataclass repr | **Fixed.** `Config.panel_api_key` is declared `repr=False`, so printing the config (or an exception that formats it) cannot leak the key. |
| Startup failure output | Prints only the `ValueError` text and a placeholder key; no real credential. |

Residual: signed download/upload URLs embed a short-lived signature (not the API
key). Treat `download_file` output as sensitive for its ~15-minute lifetime.

### 2. High-risk confirmation gates

Every declared high-risk tool calls `_denied_if_readonly()` first and
`_need_confirm()` before any network call:

- `power_signal` — stop / restart / kill require `confirm=True`; start does not.
- `write_file`, `delete_files`, `rename_file`, `upload_file` — `confirm=True`.
- `restore_backup`, `delete_backup` — `confirm=True`.

No bypass path found: validation, readonly checks, and confirm checks all run
before the corresponding `panel.*` call, and each write tool repeats the
readonly check independently.

Known limitation (by design, documented): `confirm=True` is a caller-supplied
argument. It protects against accidental model calls, not a prompt-injected or
malicious caller. For untrusted agents, run with `PANEL_READONLY=1` and a panel
API key scoped to the minimum required permissions.

Residual risk: `send_command` executes arbitrary console commands. It is blocked
in readonly mode but not confirmation-gated, because it is the product's core
interactive path. Operators who need a hard stop should use readonly mode or a
key without console permission.

