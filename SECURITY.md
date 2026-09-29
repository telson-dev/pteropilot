# Security Policy

## Reporting a Vulnerability

If you discover a security issue in PteroPilot, please **do not** open a public issue.

- Report it privately via GitHub's [**Report a vulnerability**](../../security/advisories/new) feature, or
- Email the maintainer (see the repository profile).

Please include steps to reproduce, impact, and any suggested fix. You should receive an acknowledgment within 72 hours.

## Safety Design

PteroPilot is built to operate against your own panel with minimal risk:

- **API key stays local.** Your key is read from the environment and sent only directly to your panel; it is never logged or transmitted to a third party.
- **Read-only mode.** Set `PANEL_READONLY=1` to block all power, write, delete, and restore actions.
- **Explicit confirmation.** High-risk actions (`restart`, `kill`, file overwrite/delete, backup restore/delete) require `confirm=True`.
- **Least privilege.** Prefer a scoped/limited panel API key dedicated to AI use.

## Supported Versions

While the project is pre-1.0, only the latest released version receives fixes.
