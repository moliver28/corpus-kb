# Security triage policy for scanner alerts

This repo runs static security scanners on its code. CodeQL scans the Python source on every pull request (issue #331). The mimosa plugin scans before each push. This doc tells an owner how to triage scanner alerts. It also lists the alert classes this project accepts as false positives.

## The triage workflow

Work one alert at a time.

1. Read the rule id, the message, and the flagged line.
2. Match the alert against the accepted classes in this doc.
3. On a match, dismiss the alert in the GitHub UI. The comment names the class and cites the evidence.
4. On a real finding, fix it in the change at hand, or open an issue that names the alert number.
5. On a new class, write the class and its evidence into this doc first. Then ask the repo owner to decide.

## Severity levels

CodeQL marks each alert as error, warning, or note. An error-level alert on a touched file needs a fix or a triage note in the same pull request. Note-level alerts do not block a pull request. The owner handles them during each review.

## Accepted false-positive classes

Only the classes below are accepted. Anything else needs the full workflow above.

### Vendor bundles under ui_dist/assets

The SPA build generates the minified bundles in `src/corpus_kb/ui_dist/assets/`. Never edit them by hand. Scanners match risky patterns inside that generated code. Those matches are noise. Fix real findings in `ui_src/` and rebuild. Evidence: the sweep in #216 closed #204 with two high and one medium findings left. All three sat inside vendor bundles. The sealed scan of 2026-11-17 flags shell-policy and taint patterns in the react-core, react-router, antd-core, Ingest, and Map bundles. No reviewed `ui_src/` source contains an `exec` or `child_process` call; the matched text ships inside bundled dependencies.

### Protocol stub bodies

A `typing.Protocol` method stub has one body token: `...`. CodeQL rule `py/ineffectual-statement` flags that token as a dead statement. The token is the standard spelling of an empty method stub. This class is a false positive. Evidence: alert 1174, `py/ineffectual-statement`, at `src/corpus_kb/tools/analysis_route_fallback.py:62`.

### The parser worker queue items

`src/corpus_kb/ingestion/strategies/textbook_worker.py` sends parse windows to a child process over a `multiprocessing.Queue`. One queue item is a local PDF path plus page bounds. The scanner reads the queue named `requests` and its `put` call as HTTP traffic (CWE-918). The module imports no network library, and no URL reaches it. Website URL fetches live in `corpus_kb.web.fetch`. That fetch site applies the private-address denylist, a robots.txt gate, and a 5 MiB size cap. The denylist is on by default (`allow_private_addresses`). Evidence: the sealed scan of 2026-11-17 flags `textbook_worker.py:241` at `handle.requests.put(...)`. The module imports only `multiprocessing`, `pickle`, `threading`, and `time`.

### Loopback-constant health polls

`scripts/docker_smoke.py` polls the compose-published app from the host. The target is one hardcoded constant: `APP_URL = "http://127.0.0.1:8010/app/"`. No user input reaches the URL, the scheme is fixed, and the host is loopback. The scanner reads the constant's variable name as a dynamic URL (CWE-918). Evidence: the sealed scan of 2026-11-17 flags `scripts/docker_smoke.py:65` at `urllib.request.urlopen(APP_URL, ...)`; the value is the literal on line 62. This class needs an owner decision before any dismissal.

### The fixtures recorder fetch entry

`ui_src/scripts/record-fixtures.ts` records envelope fixtures from a running dev server. Its base URL comes from the operator-set `CORPUS_KB_BASE_URL` variable. The recorder caps that value in four ways before any fetch. Only the `http:` and `https:` schemes pass. The host must be a loopback name that resolves to a loopback address. The URL must not carry embedded credentials. Each probe URL must also stay inside the validated origin. Evidence: the boundary checks sit at lines 22-40, and the per-probe origin check at lines 62-64. PR #375 hardened the file, and the scope policy excludes it.

### Localhost test servers

Tests fake network services on loopback to exercise the seams. Those servers live inside the test process on the operator machine and never leave it. The threat model treats tests as operator machines, not product attack surface. Evidence: PR #399 routed the test-pattern highs on `tests/test_textbook_worker.py` through guarded seams. The sealed scan of 2026-11-17 flags none of them.

### The mimosa scope policy

The committed `.mimosa/security-policy.json` file sets the mimosa scan scope (issue #397). It lists the paths the scanner skips. Each path there matches a false-positive class in this doc. The scanner does not consume exclusions yet (upstream reports zai-org/feedback#526 and zai-org/feedback#300), so findings stay visible until it does. The benchmark DDL findings from issue #434 are fixed in code, and the worker and recorder classes above are triaged.

## Import-cycle alerts

CodeQL flags module import cycles at two levels.

- `py/unsafe-cyclic-import` (error): a cycle that can break an import at run time. Fix it in code. Move the shared type or helper into the lower module both sides import. PR #338 used that pattern, and so did this PR.
- `py/cyclic-import` (note): a cycle that cannot break an import today. It does not block a pull request. Repair it when you touch the module anyway.

## Real findings become issues

A dismissal is never silent. Every dismissal comment names its class and cites its evidence. A real finding without an immediate fix becomes a GitHub issue. The issue names the alert number and the triage owner.

## The recommended dismissal set

After this policy merges, the owner can dismiss these alerts in one pass:

- Alert 1174 (`py/ineffectual-statement`): a Protocol stub body.
- Any later `py/ineffectual-statement` alert on a Protocol stub body.
- Any alert whose location sits under `ui_dist/assets/`.

No other class is safe for bulk dismissal. A new class needs its own evidence entry here first.
