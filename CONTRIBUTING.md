# Contributing

Help make repetitive desktop work easier: report a bug, improve the docs, or
send a focused fix. For security vulnerabilities, use the private process in
[SECURITY.md](SECURITY.md).

## Development setup

Use Python 3.10 or newer and a virtual environment. Follow the installation
instructions in [README.md](README.md#installation), activate that environment,
then install development tools:

```bash
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest -q
python -m ruff check .
python -m mypy
python -m pip_audit --skip-editable
python -m build --no-isolation
python -m twine check --strict dist/*
```

Run these commands with the virtual environment active. The generated
`requirements-dev.txt` pins runtime, development, and build dependencies with
hashes and platform/Python markers. Install it before the editable project;
`--no-deps --no-build-isolation` keeps that install on the pinned dependencies.
CI uses this set on Python 3.10 and 3.14 on macOS, Windows, and Linux. A separate
Ubuntu job installs the newest compatible `.[dev]` dependencies on both Python
versions, so the pins cannot conceal incompatibilities. Both Ubuntu jobs run the
existing suite under Xvfb with `--fail-on-skip`: a Tk fixture that cannot open a
display fails the job instead of silently skipping its GUI contracts. To repeat
that run on Linux, install Xvfb and xauth, then use
`xvfb-run -a python -m pytest -q --fail-on-skip`. Local runs and the macOS/Windows
jobs still allow skips when Tk is unavailable; use `python -m pytest -q -rs` to
see the reasons.

Real Tk tests cover modal event handling and widget behavior. The dummy input
backend and synthetic OS APIs do not verify global hooks, native focus/movement,
window stacking, DPI/multiple monitors, or macOS Accessibility permissions.
Those still need manual checks on Windows and macOS with a harmless target:
record OS/Python versions, verify Stop during preparation and playback, and
verify a covered or missing target prevents clicks. Never treat a green unit
test matrix as native desktop certification.

Ruff checks fatal errors, undefined names, bug-prone patterns (`B`), and imports
(`I`), without requiring formatting changes. Existing import ordering in
`gui.py` and `platform_support.py`, plus an unused loop variable in
`platform_support.py`, have narrow per-file exceptions pending source fixes.
Mypy checks the model, profile persistence, playback, window preparation, and
recording modules, including unannotated function bodies, using `types-pynput`
for the mouse API. This is an incremental gate; it does not yet require complete
annotations or check the GUI and platform integrations.

When changing dependencies, edit `pyproject.toml` and regenerate the pinned set
with the recorded resolver version (the universal resolution covers Python
3.10+ and all platforms):

```bash
python -m pip install uv==0.13.1
python -m uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 --generate-hashes --output-file requirements-dev.txt
```

Add `--upgrade` to refresh all pins deliberately. Review the generated diff,
then repeat the install and validation commands above in a fresh environment.
Keep the lower bounds in `.[dev]`; the newest-dependencies job intentionally
does not use the pinned file.

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
