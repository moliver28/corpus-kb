# NLP Methodology Journal

<!-- athena journal v0.1. Founded 2026-11-25 in session 466f, alongside the sources registry; this file is the journal's canonical home.
     Append a dated entry every run. -->

---

## 2026-11-25 — Founding entry (run 1; briefs/A4-athena-first-sweep.md, dispatch 1)

**Scope:** Registry bootstrap (12 seed links verified, 21 additions), D13 method validation per pillar, D14 prompt-versioning mechanism. Journal founded this run (did not exist before).

**Tool families:** brave-search, firecrawl (scrape + research paper search), tavily, built-in WebSearch/WebFetch, curl liveness checks. Four search families — requirement met.

**Beliefs formed (state: established fact → strong inference → speculation):**
1. **[Fact]** Per-type P/R/F1 with document-level or grouped bootstrap CIs is established IE evaluation practice (five independent PMC/paper sources with matching recipes, B=1,000-10,000). The D13 layer-1 recipe can copy it directly.
2. **[Fact]** Raw entity/relation-type counts never plateau in natural text (Heaps' law; RSOS 2020 evidence). Therefore "saturated" must be defined on the **coverage-based** iNEXT curve (Chao & Jost 2012), never on raw counts. This changed my belief: I initially read D13's "measured plateau" as a count plateau; that reading is not achievable and would stall every project.
3. **[Strong inference]** Capture-recapture recall with extractor + verifier as two observers is applicable **only as a bounded floor estimate**, and only with heterogeneous observers. Evidence: same-model pairs underestimate the total by ~22%, cross-family ~10% (TDS/CUAD worked example, grade C), correlated same-family errors are peer-reviewed (ICML 2025), and software-inspection data says <4 observers breaks accuracy (IEEE TSE 2000). Our verifier is same-family with our extractor → the naive two-observer setup will underestimate the unseen mass. Spec requires: heterogeneous second observer or a rules-based third observer + ~10% hand audit.
4. **[Strong inference]** No graph-compression paper gates on downstream delta-F1 — they gate on MDL cost or query preservation. Our loss gate is our own convention: delta-F1 ≤ 1.0 point AND overlapping 95% bootstrap CIs (CI clause binding). Owned assumption, stated in the registry.
5. **[Fact]** chao.site/python/iNEXT/ is dead; replaced with CRAN + iNEXT Online + Chao lab site. The UVM PDF is Gotelli & Colwell's Ch. 4 (in Magurran & McGill 2011) — the best single implementation reference (full Chao1/Chao2/ACE/ICE/jackknife formulas).
6. **[Fact]** OQuaRE metric definitions drifted across its own literature (harmonization paper); pinning our definition set is a precondition for any longitudinal audit of config/ontology.yaml.

**Verdicts:** none requested this run (A4 is bootstrap + validation, not a wave-gate review). No AM findings issued. Advisory notes for the D13 wave planner live in the registry recommendations R1-R4.

**Open questions:**
- Should the gold-set ladder (synthetic → hybrid silver → verified seeds) fix per-type minimum item counts before CI half-widths are trustworthy? Likely answer after first real gold set exists (measure, don't guess).
- Do we need observer-correlation audits (ICML 2025 style) as a standing gate, or a one-time check per model-pair choice? Lean: one-time per pair, re-run on model change.
- Can SCION's four metric tiers be mapped 1:1 onto our match rules (Literal=strict typed span; Fuzzy=overlap/embedding tier), or do we need a fifth tier for relation-argument-role accuracy? UNKNOWN until the D13 wave spec lands.
- Prompt-versioning: does promptfoo support the repo's Ollama-backed extraction provider cleanly enough that `poe prompt-eval` runs offline? Verify in the D14 implementation task; fallback = repo-native harness.

**Next-sweep seeds:**
- IRR machinery: kappa/ARI conventions for annotation agreement (math pillar names it; no source landed in the registry yet — first gap to close).
- Density floors per chunk class: find an NLP-side precedent for coverage floors on chunk-level type discovery (ecology analogs exist; NLP analogs not yet sourced).
- OLLM follow-ups: papers citing its metric suite since 2024 — check whether a graph-similarity convention has consolidated.
- MDL for ontology learning specifically (not graphs in general): likely literature in description-logic learning (e.g., DL-learner MDL objectives) — unswept.
- prompt evaluation research (prompt versioning/regression as a studied problem) for D14 evidence beyond tool docs.

**Process notes:** The founding run used interim homes. This docs PR moved both files to their canonical homes: registry = docs/research/nlp-methodology-sources.md; this journal = docs/research/nlp-methodology-journal.md. Two citation-grade entries flagged for pre-quote verification (Rissanen 1978, Heaps 1978). All seed links verified live on 2026-11-25 except the one dead replacement (documented).

---

## 2026-11-25 — Ops review (run 2; briefs/A6-athena-ops-review.md, dispatch 2)

**Scope:** Operations audit (catalogue verdicts CUT/ADD/ENRICH/KEEP), seam + generalizability of W6B/W6C (templates, translate stage, D13 ops), accuracy/reliability/latency improvements. Advisory review; verdict CHANGES REQUIRED scoped to two P1 seam findings (AM-01, AM-02). Deliverable: dossiers/ops-review.md. Registry gained pillar P5 (E1-E10).

**Tool families:** built-in WebSearch · firecrawl research paper search · brave-search · tavily. Four search families — requirement met.

**Beliefs formed:**
1. **[Fact]** The live catalogue: 85 surface rows, ~90 catalogue ops in 24 catalogue files, 33-method register, 25+ strategy files; producing chain pinned in produce.py's docstring (prepare -> store+embed -> shadow -> extract -> atomize). Verified by direct reads this run.
2. **[Fact]** `analysis_route`'s handler deletes `pg_pool`/`tenant_id` and reads no database — it cannot know a document's project. W6B-3's hook ("the document's project names a template -> template routes become the default order") is unimplementable at :134 as written, and template routes validate against catalogue OP names while the router ranks register METHOD ids. This changed my belief about the plan's readiness: W6B is generally strong but this one seam is under-pinned.
3. **[Strong inference]** Appending extraction_eval/extraction_saturation to every template route table risks the D11 interactive tiers if route resolution can select them by default; "additive tail" mitigates priority but not fall-through. NN/g's 0.1 s / 1 s / 10 s perceptual classes (registry E6) independently match D11's classes — external validity for the tier law.
4. **[Fact]** `coding_saturation` (ISR stop rule, threshold 0.05, min 50) and the planned `extraction_saturation` (coverage curve, Chao2, plateau 0.001 + C-hat >= 0.90) measure different constructs; the vocabulary must be pinned apart (AM-05).
5. **[Strong inference]** No catalogue op is a provable CUT: every family serves a register method, a template route, D13 math, or D15 SOP; release_* has the weakest use evidence (no template, no register row) but telemetry does not exist, so CUT would be speculation. Zero CUT verdicts issued, deliberately.
6. **[Fact]** demo_load and research_demo are distinct (sample-corpus loader vs narrated walkthrough; different handlers/envelopes) — not a rule-1 redundancy; naming nit only.
7. **[Speculation]** langdetect head-chars detection will misfire at a meaningful rate on code-switched UX transcripts — UNKNOWN until measured on real corpora; the R6 confidence-threshold recommendation assumes the short-text weakness generalizes to our slice length.

**Open questions:**
- Which consumer should own template-default routes: analysis_route with an explicit optional project param (catalogue row change) or a project-aware suggestions/doctor surface? Metis's call; I lean toward the explicit param + op-to-method crosswalk.
- Would per-op invocation receipts (contracts/receipts.py lineage) make the "evidence of use?" audit criterion answerable for a future CUT pass? Proposed as AM-06/R7-adjacent; owner decides.
- Relaxed-tier choice when W6C-1 lands: overlap >= 50% (S10 practice, deterministic, cheap) vs embedding >= 0.95 per triple (B2, model-dependent)? Lean overlap.
- Does B=200 bootstrap give stable coverage-slope estimates at demo-scale T (12-15 docs)? The boundary tests pin synthetic values; real variance is unmeasured (R4's CI disclosure addresses the reporting side).

**Next-sweep seeds:**
- Power/MDE math for span-F1 bootstrap at n_docs <= 20: what delta is detectable at 95%? Feeds R2's pinned underpowered bound with our own numbers.
- Krippendorff's alpha vs kappa for multi-label relation gold sets (unit-of-analysis choice) — E1 gives the machinery; our label structure picks the coefficient.
- Op-telemetry design sketch (deterministic counters, no LLM) if the owner takes AM-06.
- COMET-class QE for the translate stage stays deferred until target languages are named (E10; posture-compatible but a real model dependency).

**Process notes:** Interim homes unchanged (canonical docs/research/ copies come with the later docs PR). Registry updated with P5 (E1-E10, all verified live this run except E9 BLEU, marked citation-grade). First run-2 verdict issued (advisory CHANGES REQUIRED, scoped); re-issued BINDING only at W6C-6's gate if the findings survive to that wave.

---

## 2026-11-26 — TAO v0.3.1 doctrine verify gate (run 3; briefs/athena-v031-verify.md, dispatch 3)

**Scope:** Verdict on the proposed v0.3.1 amendment set only (AM-01–AM-07, "not measured" signals, S1, AM-06 registry corrections). No code review; T0 build gate stays at close-out. Deliverable: thematic-analysis/review/athena-v031-verdict.md — **CHANGES REQUIRED**, narrowly scoped to AM-06's E1 half.

**Tool families:** built-in WebSearch/WebFetch + direct API fetches over curl (doi.org Handle REST API, Crossref REST API, Hugging Face raw READMEs, fasttext.cc, docs.openalex.org). Verification run, not a sweep; no sweep debt added. WebSearch 429'd live during the run — AM-01's "never count a rate-limit fallback text as a source" rule earned its place the same day it was proposed.

**Beliefs formed:**
1. **[Fact]** AM-06's E1 correction is wrong on both counts: the doi.org handle for `10.1162/coli.08-034-R2` returns responseCode 100 = HANDLE NOT FOUND (the dossier read 100 as success), and Crossref 404s because the registry DOI has a transcription error — the true DOI is `10.1162/coli.07-034-r2`, which resolves both ways. This changed my belief about run-2's registry verification: "all verified live this run" was true of the ACL Anthology link, not of the DOI string; a DOI typed from a PDF can pass a resolver check on the wrong sibling string. Check DOIs with the Handle API responseCode 1/100 convention, never by eye.
2. **[Fact]** AM-01's pace numbers are accurate against current primary docs: Crossref public pool = 5 DOI lookups/s at concurrency 1, list queries 1/s (official table, fetched 2026-11-26); OpenAlex no-key 10 req/s per IP; the 217-language HF LID artifact (`facebook/fasttext-language-identification`, NLLB `lid218e`) is cc-by-nc-4.0 and `lid.176.bin` is CC-BY-SA 3.0 — the exact-artifact license rule in TD-12 point 6 is correct.
3. **[Fact]** AM-07's typology is double-sourced (QRP 2021 doi:10.1080/14780887.2020.1769238 + CAPR doi:10.1002/capr.12360); S1's JMIR paper resolves (10.2196/85572).
4. **[Strong inference]** Six of seven amendments pass; the amendment pipeline (dossier finding → applied text → verdict) worked, and the one failure was an evidence-reading error, not a design error.

**Verdicts:** CHANGES REQUIRED on the v0.3.1 set — fix F-01 (reword AM-06's E1 half: correct the DOI string to 07-034-r2, drop the "handle not Crossref" note; sites: athena-v0.3.1-proposed.md:59, decisions.md:277, row at nlp-methodology-sources.md:122 after approval). Nits F-02 (OQuaRe→OQuaRE drift), F-03 (AM-05 narrower than TD-12 point 6 — no LID model named), F-04 (AM- prefix collision, pre-existing). My findings used F-xx to escape the collision.

**Open questions:** owner call on F-03 widening; namespace disambiguation in v0.4; apply mechanics (owner or delegate edits the agent file; I land the E1/E10 row fixes on approval day).

**Next-sweep seeds:** DOI hygiene pass over all registry rows that carry a doi (Handle API responseCode check, scripted) — today's catch suggests the set may hold more typos; Crossref's Nov-2025 rate-limit regime is worth a re-read when AM-01's numbers next get quoted; re-verify E10's link upgrade to the ACL Anthology doi when the correction lands.

**Process notes:** Verdict file written per brief (review/athena-v031-verdict.md). Registry NOT touched — AM-06's corrections await owner approval and need the F-01 fix first. Journal entry appended per standing duty (docs/research/, uncommitted — main thread owns commits in worktree 6908).

**Delta re-verdict (same run, same day):** F-01 CLOSED (both sites on disk carry the correct `10.1162/coli.07-034-r2` + "re-verify against both resolvers" wording; the coordinator's quoted `10.162` typo existed only in the message, not in the files), F-02 CLOSED (OQuaRE restored). Full re-diff vs live v0.3: same 7 hunks, no new drift — also corrected my own first verdict's "ten hunks" wording (7 diff hunks; the ten-item list counted non-hunk items). **APPROVE WITH NITS** — F-03 (AM-05 width) and F-04 (AM- namespace) carried as open owner items. Lesson recorded: quote file text in verdicts, never message-quoted text; and diff-hunk counts beat prose lists for change inventories.

**Approval day (same run, same day):** Owner ruled "apply both". v0.3.1 is now the live doctrine in `~/.zcode/agents/athena.md` (verified: frontmatter line 1, header "APPROVED by owner 2026-11-26 UTC", AM-05 widened per F-03 to name translation, quality-estimation, AND language-identification models, stale "until the owner approves v0.3" sentence dropped as falsified). My sanctioned registry writes landed in `docs/research/nlp-methodology-sources.md`: row E1 — DOI string corrected to `10.1162/coli.07-034-r2` with the correction note (both-resolver verification recorded); row E10 — grade B → A with the WMT 2022 doi `10.18653/v1/2022.wmt-1.60` and the ACL Anthology link. One clause beyond the strict sanction, disclosed: I appended the checked license to E10's row (`wmt22-cometkiwi-da` cc-by-nc-sa-4.0 — never a default) because the just-applied AM-05 makes license rows binding for QE models; the main thread may strike it in the docs commit if it wants the minimal diff. F-04 (AM- namespace) parked for v0.4. TAO scope of my gate: CLOSED for v0.3.1; the T0 build wave gate (on the applied doctrine) still comes at close-out per ledger 36.

---

## 2026-11-26 — TAO T0 wave gate (run 4; thematic-analysis/brief-athena-t0.md, dispatch 4)

**Scope:** Binding gate on PR #610 (TAO T0 deterministic scaffold; branch `feature/tao-t0-scaffold`, tip `e1f6dc83`, base `b4fd61e2`). Code-only review of the 34 non-docs files at the branch tip. Deliverable: thematic-analysis/review/athena-t0-wave-verdict.md — **APPROVE WITH NITS** (1 P2 + 5 P3).

**Tool families:** none — verification run, not a sweep (run-3 precedent); no sweep debt added. Evidence basis: every quote read via `git show`/`git grep` at the tip. Verified the worktree checkout is stale against the tip (203 insertions / 2895 deletions missing), so disk reads would have quoted stale code. Lesson generalized: in review runs on fetched worktrees, diff and show from git, never the checkout.

**Beliefs formed:**
1. **[Fact]** AM-07 is enforced in code, not text: `VALID_PARADIGMS` membership raise at codebook.py:118-121 (tip), and `load_codebook` validates before any write. The wave law holds: no generative call anywhere in T0 — every new module is regex, SQL, CSV, hashing, or linear algebra (all new modules read).
2. **[Fact]** The 59-seed grade-A bar is exact Clopper-Pearson arithmetic: zero-failure one-sided lower bound `0.05^(1/n) ≥ 0.95` → n = 59 (n = 58 gives 0.9497). The `seed_bar` block also separates the 8 packaged planted-error rows from the calibration-seed bar in its own note text — honesty worth copying into later gate reports.
3. **[Strong inference]** The composed-math shape (mece_report composing research/overlap + exhaustiveness + cluster_stability instead of recomputing) is the right elegance pattern for TAO reports; signatures re-verified against their definitions (`calibrate_tau_res` floor 0.05 exact match; `residuals()` call shape).
4. **[Fact]** AM-02's "every metric" binds continuous means too: T0 shares carry Wilson intervals; centroid distance, silhouette, and stability ARI carry only n. That is the one P2, pinned to T1 before any quality claim cites these numbers.

**Verdicts:** APPROVE WITH NITS on TAO T0 (PR #610). Findings `TAO-T0-01..06` (namespace dodges the AM- collision, per run-3 F-04): P2 intervals on continuous metrics (T1 deadline); P3 positive-only silhouette mean bias, embedder fallback default vs "unknown", no-op naive-datetime branch in `_cell`, `record_step` planned-kind guard, unconsumed `signal_pairs` fetch. Pins carried forward: paradigm-gated wiring of `keyword_tag_decision` before it touches live paths; keyword retire-on-reload before the block rule wires (builder deviation 6); `format_version` belongs to the T3 package manifest — the CSV set must not travel as a share without it.

**Open questions:** does `bootstrap_stability`'s 5-resample ARI need more resamples at real corpus scale (fixture-scale today)? Owner call on TD-18 amend-or-cut (LOC 1,309 vs cap 645). Who owns the T3 manifest's import-refusal contract (importers refuse newer majors per AM-03)?

**Next-sweep seeds:** Wilson-vs-bootstrap conventions per metric kind (share vs mean) — one registry row would pin the report recipe (E1-adjacent); audit `research/` consumers of `silhouette_by_group` for positive-filter precedents before T1 changes the report shape; capture-recapture observer notes (S8 minimum-observers rule) meet the T2 bench seed design.

**Process notes:** Verdict file written per brief (review/athena-t0-wave-verdict.md). Registry untouched — no new sources; this run was a code gate. Brief re-read at each milestone; no appended work arrived. Journal uncommitted — the main thread owns commits in worktree 6908.
