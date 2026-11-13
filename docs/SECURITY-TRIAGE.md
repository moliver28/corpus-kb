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

The SPA build generates the minified bundles in `src/corpus_kb/ui_dist/assets/`. Never edit them by hand. Scanners match risky patterns inside that generated code. Those matches are noise. Fix real findings in `ui_src/` and rebuild. Evidence: the sweep in #216 closed #204 with two high and one medium findings left. All three sat inside vendor bundles.

### Protocol stub bodies

A `typing.Protocol` method stub has one body token: `...`. CodeQL rule `py/ineffectual-statement` flags that token as a dead statement. The token is the standard spelling of an empty method stub. This class is a false positive. Evidence: alert 1174, `py/ineffectual-statement`, at `src/corpus_kb/tools/analysis_route_fallback.py:62`.

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
