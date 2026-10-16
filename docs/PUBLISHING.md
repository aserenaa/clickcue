# Publication checklist

Run this from the repository root before the first push and each release.
Repository publication and publishing a package to PyPI are separate actions;
the CI workflow does neither automatically.

## 1. Review exactly what will leave your computer

```bash
git status --short --untracked-files=all
git ls-files --cached --others --exclude-standard
git status --short --ignored
```

Check the file list yourself. Local profiles, credentials, `.env` files,
private keys, virtual environments, build output, and machine-specific notes
do not belong in Git. Ignore rules do not protect already tracked files or
files added with `git add --force`. Inspect screenshots and non-text files
manually. Public author names in LICENSE and package metadata are intentional;
also review your Git author name/email before creating commits.

Stage only reviewed files. Then inspect `git diff --cached --stat` and
`git diff --cached`. Never paste unredacted scan findings into an issue.

## 2. Scan files and history

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then run:

```bash
gitleaks dir --redact --no-banner .
# After the first commit, also inspect every available Git commit:
gitleaks git --redact --no-banner --log-opts="--all" .
```

The directory scan includes ignored local files and may take longer in a
virtual environment; investigate findings privately and distinguish ignored
local data from the actual upload set. CI scans full fetched Git history.
If a secret was ever committed, rotate it and clean the affected history
before publishing. A clean scan cannot prove that no sensitive data exists;
manual review is still necessary.

## 3. Verify the release

With the virtual environment active:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m pip_audit --skip-editable
python -m build
python -m twine check --strict dist/*
```

Inspect both the wheel and source archive. They should contain source,
synthetic tests, documentation, licenses, and package metadata, never private
profiles, credentials, or virtual environments. Build from a clean reviewed
checkout for the actual release. Audit the resolved dependencies on each
supported OS; optional platform dependencies differ. A vulnerability scan
checks known advisories, not all possible vulnerabilities.

Try installation and launch in a fresh environment. Manually test capture,
copying, countdown, save/load, F6 stopping, and a covered target using harmless
windows. Never claim platform GUI support based on unit tests alone.

## 4. Configure GitHub before public use

- Confirm `aserenaa/clickcue` is the intended repository name and all project
  links are correct.
- Enable private vulnerability reporting, secret scanning, and push protection
  where available. Verify the security-report link works.
- Enable Dependabot alerts and security updates. The checked-in Dependabot
  configuration also requests weekly dependency and action updates.
- Require CI status checks before merging to `main`, block force pushes and
  branch deletion, and apply the rule to administrators where appropriate.
  Select the check names after the first CI run creates them.
- Review Actions permissions: workflows need read access only and no deployment
  credentials. Keep action references pinned to reviewed commit hashes.
- Wait for all CI jobs to pass before announcing the repository. Review the
  changelog, license, and security policy once more.

These settings require an actual GitHub repository. They are not enabled by
adding files locally. A future PyPI release also needs a separate review of
package-name availability, versioning, included files, and publishing access.
