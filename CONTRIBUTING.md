# Contributing

Help make repetitive desktop work easier: report a bug, improve the docs, or
send a focused fix. For security vulnerabilities, use the private process in
[SECURITY.md](SECURITY.md).

## Development setup

Use Python 3.10 or newer and a virtual environment. Follow the installation
instructions in [README.md](README.md#installation), activate that environment,
then install development tools:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m pip_audit --skip-editable
python -m build
python -m twine check --strict dist/*
```

Run these commands with the virtual environment active. CI tests Python 3.10
and 3.14 on macOS, Windows, and Linux. These are automated unit tests, not a
certification of GUI, permissions, or window behavior on every OS.

## Testing without real clicks

Tests must not send real input, require Accessibility permission, depend on
personal profiles, or need network access. `tests/conftest.py` forces pynput's
dummy backend before collection; playback tests replace mouse controllers
with mocks. Keep that isolation when adding tests. Use synthetic fixtures.

## Pull requests

Keep changes focused and match existing style. Add meaningful regression
coverage for changed behavior and update user-facing documentation. For
input or window changes, describe a manual check using a harmless window,
including the OS, Python version, stopping behavior, and covered-target check.
Never use production forms or purchases for testing.

Open a pull request against `main`; direct pushes are protected. All required
CI checks must pass, the branch must be up to date, and review conversations
must be resolved before merging. Explain the problem, the resulting behavior,
and how you verified it. Update the README when controls or behavior change.

## Bug reports and feature requests

Use the [issue forms](https://github.com/aserenaa/clickcue/issues/new/choose).
For a bug, include the OS, Python and ClickCue version or commit, a small
reproduction, and expected versus actual behavior. For a feature, describe
the workflow and the outcome you need.

## Privacy

Do not include personal profiles, credentials, private step names/window
titles, or unsanitized screenshots in issues or pull requests. Follow
[SECURITY.md](SECURITY.md) for private vulnerability reports.
