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

## Security Review — New read-only tools (2026-10-26)

Scope: the three tools added after the 2026-10-20 audit — `list_players`,
`check_server_health`, `batch_get_status` — verified against the same three
questions, with regression tests in `tests/test_security_readonly_tools.py`.

### 1. API key / sensitive-info exposure

No leak. All three tools reach the panel only through `PanelClient` read methods
(`online_players` → `console_log`, `resources`, `client_servers`), which all
route through `_Guarded` → `redact()`. Panel error bodies and transport
exceptions are therefore stripped of `Bearer <token>` and `ptlc_/ptla_/peli_`
keys before reaching the model. `batch_get_status` goes further: its per-server
try/except swallows the exception entirely and emits a generic
「查询失败（超时或面板报错）」 line, with **no** error detail at all.

### 2. Read-only enforcement / bypass

No bypass path. These are read-only (GET-only) tools and deliberately do **not**
call `_denied_if_readonly()` — that guard exists to block *write* operations, and
there is nothing to block here. Verified that they issue only `GET` requests and
never touch any write/dangerous client method (`power`, `send_command`,
`write_file`, `delete_files`, `restore_backup`, `delete_backup`, `upload_file`,
`create_folder`, `create_backup`). Running under `PANEL_READONLY=1` still works
for all three, as intended.

### 3. Internal detail leakage to the model

None. Outputs contain only server identifiers, names, `current_state`, derived
health level, and player names — no API key, no bearer token, no signed URLs.
(Contrast `download_file`, which returns a short-lived signed URL and is treated
as sensitive — that tool predates this review and is unchanged.)

## Security Review — alerts.py notification secrets (2026-10-28)

Scope: the `pteropilot-alerts` module (`src/pteropilot/alerts.py`), which
introduces two new secret-bearing surfaces absent from prior audits — the
webhook URL (which may end in a signed `?access_token=***` / `?key=***` query)
and SMTP credentials (`--smtp-pass`). Answers below.

### 1. Do error messages / exception stacks leak the webhook token or SMTP password?

**Webhook token — fixed.** `send_webhook` uses a bare `httpx.AsyncClient` (not
`_Guarded`), so a push failure (e.g. `raise_for_status()` →
`HTTPStatusError`) carries the *full signed URL* in its message. Without
redaction this would surface the `access_token`/`key` in the scan's `errors`
list (which `main()` then prints). `scan_and_notify` now wraps its per-server
exception handler with `redact(str(exc))`, and `_SECRET_PATTERNS` in
`client.py` gained two regexes — `access_token=[...]` and `[?&]key=[...]` —
which strip these tokens to `***`.

**SMTP password — no leak.** `send_email` raises
`smtplib.SMTPAuthenticationError`/`SMTPException`, whose message is the *server's*
response (`(535, b'...')`) — never the password. The password is passed
positionally to `s.login(user, password)` and is not echoed. Verified by test
`test_smtp_auth_failure_does_not_leak_password`: the failure path receives the
real password but the resulting `errors` entry omits it.

### 2. Does `alerts_state.json` (`~/.pteropilot/`) store sensitive data?

No. `save_state` persists only `{state, hits, ts}` per server identifier — no
webhook URL, no SMTP credentials, no API key. The `alerts.json` config file is
user-authored (may contain per-server `webhook_url`), but that is explicit config
under the operator's control, not telemetry. Verified by
`test_alerts_state_file_contains_no_credentials`.

### 3. Does CLI `--help` / logging print credentials?

No. `argparse` help shows only the `help=` text, never the resolved default values
(from `SMTP_PASS`/`SMTP_USER` etc.), and nothing logs request headers or the
configured secrets. `main()` prints only channel names, counts, and
already-redacted `errors`.

Regression coverage added in `tests/test_alerts.py` under the heading
「密钥泄漏安全复审（O2）」: `redact` strips `access_token`/`key` (and still
`Bearer`/`ptla_`/`ptlc_`/`peli_`), webhook push failures with a signed URL do not
leak the token into `errors`, and the SMTP auth-failure path does not leak the
password.

