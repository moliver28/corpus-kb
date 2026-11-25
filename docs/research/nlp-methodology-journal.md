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
