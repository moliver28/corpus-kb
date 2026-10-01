# Rollback Drill

This document describes how to roll back a bad `corpus-kb` release and how to
run a low-risk drill to verify the rollback path before it is needed.

The release pipeline (`.github/workflows/release.yml`) uses **Trusted
Publishing** (OIDC) to upload to PyPI. No API token is stored in GitHub.

---

## PyPI Trusted Publishing prerequisite

Before the release pipeline can publish, a PyPI project admin must add a
trusted publisher:

1. Go to **https://pypi.org/manage/project/corpus-kb/settings/publishing/**
2. Click **Add a new pending publisher**.
3. Fill in:
   - **Publisher**: GitHub
   - **Repository**: `moliver28/corpus-kb`
   - **Workflow name**: `release.yml`
   - **Environment**: `pypi`
4. Save the pending publisher.

The GitHub Actions environment (`pypi`) is enforced by the workflow, so only
jobs running in that environment can request the OIDC token PyPI accepts.

---

## Normal release

1. Ensure `pyproject.toml` contains the intended version.
2. Create and publish a GitHub release.
3. The `release.yml` workflow triggers automatically, builds a wheel and sdist
   with `python -m build`, and publishes them to PyPI.

---

## Rollback steps

If a published release is broken:

1. **Yank the release on PyPI**.
   - Go to **https://pypi.org/manage/project/corpus-kb/releases/**.
   - Find the broken version and click **Yank**.
   - Yanking prevents new installs of that version while leaving the file in
     place for users who already pinned it.
2. **Stop the broken GitHub release from being the latest**.
   - Edit the GitHub release and mark it as a **pre-release**, or delete the
     release and its tag if it should never be used again.
3. **Ship a fixed version**.
   - Apply the fix on a branch, bump the version in `pyproject.toml`, and open
     a pull request through the normal CI gate.
   - After merge, publish a new GitHub release. The pipeline uploads the fixed
     version to PyPI.

> Do **not** reuse the same version string for a replacement upload. PyPI does
> not allow overwriting an existing file, even after yanking or deleting it.

---

## Rollback drill

Run this drill with a **pre-release** version so real users do not pick it up
accidentally.

### 1. Prepare a throwaway pre-release

```bash
# Use a branch for the drill, e.g. release/0.1.0a1-drill
git checkout -b release/0.1.0a1-drill
```

Temporarily set the version in `pyproject.toml`:

```toml
version = "0.1.0a1"
```

Commit and push the branch. Then create a GitHub **pre-release** from that
commit with tag `v0.1.0a1`.

### 2. Verify the pipeline publishes the pre-release

Publishing the pre-release triggers `release.yml`. Confirm the workflow run
succeeds and the version appears at:

```text
https://pypi.org/project/corpus-kb/0.1.0a1/
```

Install it to confirm it is live:

```bash
pip install corpus-kb==0.1.0a1
```

### 3. Yank the pre-release

In the PyPI release management page, yank version `0.1.0a1`.

### 4. Verify the rollback

Attempting to install the yanked version should fail:

```bash
pip install corpus-kb==0.1.0a1
```

Expected output contains something like:

```text
Could not find a version that satisfies the requirement corpus-kb==0.1.0a1
```

### 5. Clean up

- Unyank or delete the drill version on PyPI if you want to reuse it later.
- Delete the GitHub pre-release and tag.
- Revert the temporary version change.

---

## Rollback checklist

- [ ] PyPI trusted publisher is configured for the `pypi` environment.
- [ ] Broken version has been yanked on PyPI.
- [ ] GitHub release is marked as pre-release or deleted.
- [ ] A new, higher version has been prepared and merged.
- [ ] New GitHub release has been published and the pipeline is green.
- [ ] `pip install corpus-kb` resolves to the fixed version.
