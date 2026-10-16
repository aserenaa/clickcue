# Contributing

Use Python 3.10 or newer and a virtual environment. Follow the installation
instructions in README.md, then install development tools:

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

Tests must not send real input, require Accessibility permission, depend on
personal profiles, or need network access. `tests/conftest.py` forces pynput's
dummy backend before collection; playback tests replace mouse controllers
with mocks. Keep that isolation when adding tests. Use synthetic fixtures.

Keep changes focused and match existing style. Add meaningful regression
coverage for changed behavior and update user-facing documentation. For
input or window changes, describe a manual check using a harmless window,
including the OS, Python version, stopping behavior, and covered-target check.
Never use production forms or purchases for testing.

Do not include personal profiles, credentials, private step names/window
titles, or unsanitized screenshots in issues or pull requests. Follow
[SECURITY.md](SECURITY.md) for private vulnerability reports.
