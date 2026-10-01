# Release Pipeline Dry-Run Evidence

**Date:** 2026-09-30  
**Worktree:** `F:\Documents\OpenCode\Corpus\_worktrees\install-ux-pr3`  
**Package:** corpus-kb 0.1.0  
**Python:** C:\Users\moliv\anaconda3\python.exe  
**Goal:** Verify wheel/sdist build, `twine check`, and upload preview without publishing to real PyPI.

## 1. Environment / Tool Versions

```text
build 1.5.0
twine version 7.0.0 (readme-renderer: 46.0, requests: 2.34.2, requests-toolbelt: 1.0.0, urllib3: 2.7.0, keyring: 23.13.1, rfc3986: 2.0.0, rich: 15.0.0, packaging: 26.2, id: 1.6.1)
```

`twine` was not initially installed; installed via `python -m pip install twine` for this dry-run.

## 2. Build: `python -m build --outdir dist`

Command:

```powershell
C:\Users\moliv\anaconda3\python.exe -m build --outdir dist
```

Result: **SUCCESS**

```text
Successfully built corpus_kb-0.1.0.tar.gz and corpus_kb-0.1.0-py3-none-any.whl
```

## 3. Artifact Inventory

| File | Size | SHA-256 |
|------|------|---------|
| `dist/corpus_kb-0.1.0-py3-none-any.whl` | 105.5 KB (108,044 bytes) | `9cd336a25a2ba9cca2f6f4439b8f111f982061285ec6197b1738243fc9166313` |
| `dist/corpus_kb-0.1.0.tar.gz` | 126.8 KB (129,800 bytes) | `3f8e92959e8b94a1199650f31919c7ff0d47f6c616ad1920d8e8071c0bd15fac` |

## 4. Twine Check

Command:

```powershell
C:\Users\moliv\anaconda3\python.exe -m twine check dist/*
```

Result: **PASSED**

```text
Checking dist\corpus_kb-0.1.0-py3-none-any.whl: PASSED
Checking dist\corpus_kb-0.1.0.tar.gz: PASSED
```

## 5. Publish Preview (Dry-Run)

Command (no credentials provided; expected to fail safely before any upload):

```powershell
C:\Users\moliv\anaconda3\python.exe -m twine upload --repository testpypi --non-interactive --verbose dist/*
```

Result: **Upload preview shown; no files uploaded** (error is missing TestPyPI credentials, which is expected in a dry-run environment).

```text
INFO     Using configuration from C:\Users\moliv\.pypirc
Uploading distributions to https://test.pypi.org/legacy/
INFO     dist\corpus_kb-0.1.0-py3-none-any.whl (105.5 KB)
INFO     dist\corpus_kb-0.1.0.tar.gz (126.8 KB)
INFO     username set by command options
INFO     Querying keyring for password
INFO     Trying to use trusted publishing (no token was explicitly provided)
WARNING  This environment is not supported for trusted publishing
ERROR    NonInteractive: Credential not found for API token.
```

This confirms the artifacts are recognized by twine and the upload path is configured for TestPyPI. No network upload occurred.

## 6. Lint Verification

Command:

```powershell
ruff check src/ tests/
```

Result: **PASSED** (no output means no issues).

## 7. Conclusion

- Wheel and sdist build cleanly with `python -m build`.
- `twine check` passes for both artifacts.
- Twine upload preview resolves the correct TestPyPI URL and lists both artifacts; it halts at authentication, which is the intended safe behavior for a dry-run.
- `ruff check src/ tests/` remains clean.

Release pipeline is ready for the Trusted Publishing workflow in `.github/workflows/release.yml` to perform the actual publish.
