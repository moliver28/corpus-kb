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
