# Security policy

**Found a vulnerability? [Report it privately](https://github.com/aserenaa/clickcue/security/advisories/new).**
For ordinary bugs and feature requests, use the
[issue forms](https://github.com/aserenaa/clickcue/issues/new/choose).

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/aserenaa/clickcue/security/advisories/new)
under **Security → Report a vulnerability**. Do not post vulnerability details
in a public issue. If private reporting is unavailable, ask the maintainer for
a private contact channel without including the vulnerability details.

Include the commit or version, OS, Python version, and a minimal reproduction
using synthetic step names and a harmless test window. Never attach credentials,
private profiles, sensitive window titles, or unsanitized screenshots or logs.
The current `main` branch is the supported version; older versions may require
an upgrade to receive fixes.

## Data and permissions

- Profiles are plain JSON in `~/.clickcue/profiles/`. They store step names,
  screen coordinates, delays, loop settings, and the target app/window identity
  and bounds. On Windows the identity is a window title and may contain private
  information. Profiles are not encrypted; access depends on filesystem permissions.
- ClickCue does not send profiles anywhere, collect telemetry, run a network
  service, or record typed text. A global keyboard listener handles F6/F8.
- On macOS, Accessibility allows input synthesis and window control; Input
  Monitoring supports the global hotkeys. Grant permissions only to a trusted
  launcher, and revoke them when no longer needed.
- Installing and auditing dependencies contacts external package services.

## Automation limits

Only run profiles you have reviewed. A profile is data, but it controls real
mouse clicks that can submit forms, change settings, or trigger purchases.
ClickCue does not evaluate code from profiles.

Window guards are best effort. They identify macOS apps by name and Windows
windows by title, not by a secure identity. Another window from the same app
or with the same title may match; window contents can change after a check.
Linux has no window anchoring or covering-window guard. Playback without a
target uses absolute coordinates, and a missing target can be explicitly
bypassed by the user. F6 requests a stop, but an in-progress click or window
focus attempt may finish first. Use a harmless window to test every sequence.

## Publication hygiene

Do not commit `.env` files, credentials, private keys, local profile exports,
or screenshots/logs containing personal data. Git exclusions are a convenience,
not a security boundary. Review the exact files being staged and run the scans
in [docs/PUBLISHING.md](docs/PUBLISHING.md). If a credential ever reaches Git,
revoke or rotate it; deleting the latest file does not remove it from history.
