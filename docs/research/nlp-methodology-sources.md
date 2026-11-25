# NLP Methodology Sources Registry

<!-- athena registry v0.1. Founded 2026-11-25 in session 466f; this file is the registry's canonical home.
     Every entry verified live on 2026-11-25 unless marked. -->

**Scope served:** D13 math-governed extraction quality (BENCHMARK / SATURATION / COMPRESSION) + MECE & future-proofing + D14 prompt versioning.

**Grading key:** A = peer-reviewed paper or benchmark (incl. canonical books/preprints with accepted venue noted) · B = official repo, leaderboard, standards body, vendor docs · C = reputable secondary (blog/trade press). Each technique claim needs ≥2 independent sources + one stated caveat (owner rule).

**Tool families used this sweep (≥3 required):** brave-search · firecrawl (scrape + research paper search) · tavily · built-in web search/fetch · curl liveness checks. Four search families + transport checks. Logged per Athena contract.

---

## P1 — BENCHMARK (gold sets, per-type P/R/F1, bootstrap CIs, graph similarity)

| # | Source | Link | Grade | Tags | Use for |
|---|--------|------|-------|------|---------|
| B1 | Hu et al., "SCOPE and SCION: A Benchmark and an Auditable Reference Pipeline for Schema Induction and Fusion from Text" (arXiv 2607.21610) | arxiv.org/abs/2607.21610 | A (preprint; benchmark paper) | gold-schema, benchmark, schema-induction | Gold-schema benchmark design; its four metric tiers (Literal / Fuzzy / Continuous / Graph) are the defensible vocabulary for match strictness levels. Seed. |
| B2 | Ghanem & Cruz, "Enhancing Knowledge Graph Construction: Evaluating with Emphasis on Hallucination, Omission, and Graph Similarity Metrics" (arXiv 2502.05239) | arxiv.org/abs/2502.05239 | B (preprint) | KG-eval, hallucination, omission, graph-similarity | Precedent for separating hallucination vs omission error classes and for a pinned fuzzy-match threshold (BERTScore ≥ 0.95 per triple for graph matching). Seed. |
| B3 | Lo et al., "End-to-End Ontology Learning with Large Language Models" (OLLM, arXiv 2410.23584) | arxiv.org/abs/2410.23584 | A (benchmark methods; ICLR-paper lineage) | ontology-learning, graph-similarity, eval-metrics | Semantic+structural similarity metrics for ontology graphs; shows why plain F1 against one target ontology is brittle. Seed. |
| B4 | Šimsa et al., "DocILE Benchmark for Document Information Localization and Extraction" (ICDAR 2023, arXiv 2302.05658) | arxiv.org/abs/2302.05658 | A | benchmark, IE, per-class-eval | Model for per-class evaluation at fine grain (55 field classes) with a pinned primary metric; zero/few-shot test splits. Added. |
| B5 | Zhou et al., "UniversalNER: Targeted Distillation from LLMs for Named Entity Recognition" (ICLR 2024, arXiv 2308.03279) | arxiv.org/abs/2308.03279 | A | NER, benchmark, per-type-eval | Per-type NER evaluation convention and cross-type generalization evidence; model-side baseline within ≤8b scope. Added. |
| B6 | Koehn, "Statistical Significance Tests for Machine Translation Evaluation" (ACL 2004, P04-1037) | aclanthology.org/P04-1037/ | A | bootstrap, significance-testing | Canonical bootstrap-resampling citation for NLP evaluation; the CI machinery the benchmark layer cites. Added. |
| B7 | Elvas & Carvalho, domain-vs-general LLM medical IE benchmark (PMC13462776, 2026) | pmc.ncbi.nlm.nih.gov/articles/PMC13462776/ | A | bootstrap-recipe, doc-level-resampling | Exact recipe: resample documents, re-aggregate TP/FP/FN per resample, 95% CI for P/R/F1 under strict and relaxed match. Added. |
| B8 | Keck et al., species-interaction extraction from ecological literature (PMC13713224, 2026) | pmc.ncbi.nlm.nih.gov/articles/PMC13713224/ | A | bootstrap-recipe, grouped-bootstrap, relation-extraction | Grouped bootstrap (groups = source paragraphs) to respect within-document correlation — the correct resampling unit for relation extraction. Added. |
| B9 | Chuang et al., self-prompting SLM clinical IE (PMID 43044941, 2026) | pubmed.ncbi.nlm.nih.gov/43044941/ | A | bootstrap-recipe, soft-match, decision-rule | Soft-match entity-level F1 + bootstrap CIs; uses non-overlapping CIs as the improvement criterion — precedent for our gate rule. Added. |
| B10 | Chacoma & Zanette, "Heaps' Law and Heaps functions in tagged texts" (Royal Society Open Science 7:200008, 2020) — cross-listed, see S6 | (see S6) | — | — | (indexed under Saturation) |

**Caveat (pillar-wide):** strict vs relaxed matching changes F1 by several points in all reviewed sources; the match rule must be pinned in config before any comparison is meaningful.

## P2 — SATURATION (accumulation curves, Chao/iNEXT, Heaps, capture-recapture)

| # | Source | Link | Grade | Tags | Use for |
|---|--------|------|-------|------|---------|
| S1 | Gotelli & Colwell, "Estimating species richness", Ch. 4 in Magurran & McGill (eds), *Biological Diversity* (OUP 2011) | uvm.edu/~ngotelli/manuscriptpdfs/Chapter%204.pdf | A | chao1, chao2, rarefaction, formulas | The implementation reference: exact Chao1/Chao2 (incl. bias-corrected forms + variances), ACE, ICE, jackknife; accumulation vs rarefaction curve distinction. Seed. |
| S2 | Chao et al., "Rarefaction and extrapolation with Hill numbers" (Ecological Monographs 84:45-67, 2014) | (cited by CRAN iNEXT refman + iNEXT Online; paper) | A | iNEXT-theory, hill-numbers, coverage | Theory behind the curves we adapt; coverage-based standardization is the saturation metric of record. Seed (Chao lab iNEXT entry). |
| S3 | iNEXT R package (CRAN) + iNEXT Online (shinyapps) + Chao lab software page | cran.r-project.org/web/packages/iNEXT/index.html · chao.shinyapps.io/iNEXTOnline/ · sites.google.com/view/chao-lab-website/software/inext | B | implementation, estimator-software | Reference implementation of Chao estimators + coverage-based R/E curves; the API shape the Python port should copy. Seed REPLACEMENT: chao.site/python/iNEXT/ is DEAD (connection failure 2026-11-25); these three live links replace it. |
| S4 | Chacoma & Zanette, Heaps' law in tagged texts (RSOS 2020, PMC7137977) | pmc.ncbi.nlm.nih.gov/articles/PMC7137977/ | A | heaps-law, type-token, tagged-text | Empirical Heaps curves on POS-tagged corpora; evidence that raw type counts never plateau in natural text → saturation must be coverage-defined, not count-defined. Seed. |
| S5 | Heaps, *Information Retrieval: Computational and Theoretical Aspects* (Academic Press 1978) | (book; canonical, cited by S4 lineage) | A | heaps-law, canonical | Origin of the vocabulary-growth law; cited via S4 + standard IR literature. Citation-grade entry (book not fetched this run). |
| S6 | Chao, "Estimating the population size for capture-recapture data with unequal catchability" (Biometrics 43:783-791, 1987) | doi.org/10.2307/2531432 (JSTOR; PDF copy gwern.net/doc/statistics/order/capture/1987-chao.pdf) | A | capture-recapture, heterogeneity | Primary for heterogeneous catchability — the exact failure mode of extractors that find some types easily. Found as the primary behind the C-grade seed. |
| S7 | Burnham & Overton, "Estimation of the size of a closed population when capture probabilities vary" (Biometrika 65:625-633, 1978) | doi.org/10.1093/biomet/65.3.625 (bot-wall 403; citation verified via A11 refs + Biometrika record) | A | jackknife, capture-recapture | The jackknife-k estimator used when ≥3 observers exist; secondary estimator behind the capture-recapture recipe. Added. |
| S8 | Briand et al., "A comprehensive evaluation of capture-recapture models for estimating software defect content" (IEEE TSE 26:518-540, 2000) | doi.org/10.1109/32.852741 | A | capture-recapture, software-inspection, minimum-observers | Non-ecology validation: with fewer than four inspectors, capture-recapture models were not accurate enough — the strongest anchor for our minimum-observer rule. Added. |
| S9 | Kim et al., "Correlated errors in large language models" (ICML 2025, PMLR v267) | proceedings.mlr.press/v267/kim25e.html | A | correlated-errors, independence-violation | Peer-reviewed proof that same-family models share blind spots — quantifies why extractor+verifier from one family break the independence assumption. Added. |
| S10 | Bilychenko, "How to Estimate AI Agent Recall With Capture-Recapture" (Towards Data Science, 2026-11-09) | towardsdatascience.com/how-to-estimate-the-recall-of-ai-agents-with-capture-recapture/ | C | worked-example, agent-recall, audit | Worked example on CUAD (294 contracts): same-model pairs underestimate total by 22%; cross-family ~10% low; 10% hand audit lands within 2 points; pooled recall overstates by 6-12 points. Grade C by rule; primaries are S6/S7/S8/S9. Seed. |
| S11 | Bilychenko, eval-capture-recapture repo | github.com/mbilychenko/eval-capture-recapture | B | implementation, code | Re-runnable code + matched result CSVs backing S10; the reference implementation for a Chapman estimator helper. Added. |
| S12 | Hendrycks et al., CUAD (arXiv 2103.06268) | arxiv.org/abs/2103.06268 | B | benchmark, legal-contracts, gold-set | Labeled contract corpus used to grade S10's estimator; candidate synthetic→silver gold-set source for a contracts-shaped corpus. Added. |
| S13 | Colwell et al., "Models and estimators linking individual- and sample-based rarefaction" (Ecology, 2012) + Chao & Jost coverage-based R/E (2012) | (cited by CRAN iNEXT refman) | A | rarefaction, sample-coverage | The sample-coverage definition used for "density floors per chunk class". Citation-grade via official package docs. Added. |

## P3 — COMPRESSION (MDL over graphs, FCA reduction, transitive reduction, loss gates)

| # | Source | Link | Grade | Tags | Use for |
|---|--------|------|-------|------|---------|
| C1 | Cook & Holder, "Substructure Discovery Using Minimum Description Length and Background Knowledge" (JAIR 1:231-255, 1994) | arxiv.org/abs/cs/9402102 | A | mdl, substructure, subdue | The MDL-over-graphs foundation: compress input, best substructure = best compressor; inexact graph match bound. Seed. |
| C2 | Lee et al., "SSumM: Sparse Summarization of Massive Graphs" (KDD 2020, arXiv 2006.01060) | arxiv.org/abs/2006.01060 | A | graph-summarization, mdl, budget | Modern MDL graph summarization with an explicit bit budget k — the "compression target" knob for ontology compaction. Added. |
| C3 | Belth et al., "What is Normal, What is Strange, and What is Missing in a KG" (WWW 2020, arXiv 2003.10412) | arxiv.org/abs/2003.10412 | A | kg-refinement, mdl-rules, summarization | MDL-described soft rules that double as anomaly + missing-edge detectors — reuse as the zero-yield/prune criterion over relation types. Added. |
| C4 | Aho, Garey & Ullman, "The transitive reduction of a directed graph" (SIAM J. Computing 1(2):131-137, 1972; DOI 10.1137/0201010) | epubs.siam.org (DOI) | A | transitive-reduction, dag, canonical | The canonical transitive-reduction algorithm (fewest edges, same reachability) — direct implementation target for the graph-reduction step. Added. |
| C5 | Fan et al., "Making graphs compact by lossless contraction" (PMC9845199, VLDB 2022) | pmc.ncbi.nlm.nih.gov/articles/PMC9845199/ | A | lossless, contraction, query-preservation | Defines lossless vs lossy compression with query-class preservation — the vocabulary for our loss-gate: what must survive compression. Added. |
| C6 | Rissanen, "Modeling by shortest data description" (Automatica 14:465-471, 1978) | (canonical; not fetched this run) | A | mdl, canonical | MDL principle origin; cite for L(H)+L(D|H) framing. Citation-grade entry; verify before any gate code quotes it. Added. |
| C7 | Wan & Wei, "Attribute Reduction Based on Property Pictorial Diagram" (The Scientific World Journal 2014:109706, PMC4163284) | pmc.ncbi.nlm.nih.gov/articles/PMC4163284/ | A | fca, attribute-reduction, lattice | FCA attribute reduction that preserves lattice structure — the math for dropping redundant type columns without changing the concept lattice shape. Seed. |
| C8 | Zhao, Zhang et al., "Matching biomedical ontologies based on formal concept analysis" (FCA-Map, J. Biomedical Semantics 9:11, 2018, PMC5859804) | pmc.ncbi.nlm.nih.gov/articles/PMC5859804/ | A | fca, ontology-matching, merge | Five-context FCA method for finding equivalent/complex mappings — the merge-near-equivalent-concepts recipe. Seed. |
| C9 | GO subsets (slims) guide — Gene Ontology documentation | geneontology.org/docs/go-subset-guide | B | slim, pruning, practice | Lived practice of maintained reduced subsets ("slims", incl. anti-slim exclusion lists) — operational precedent for pruning zero-yield types while keeping the full ontology addressable. Added. |

**Caveat (pillar-wide):** no reviewed graph-compression paper gates compression on a downstream delta-F1; they gate on MDL cost or query preservation. Our delta-F1 gate is OUR convention (see recommendations), and must be owned as such.

## P4 — MECE & FUTURE-PROOFING (OBO principles, OQuaRE, OntoClean, identifiers)

| # | Source | Link | Grade | Tags | Use for |
|---|--------|------|-------|------|---------|
| M1 | OBO Foundry Principles (FP-000 summary, live normative set) | obofoundry.org/principles/fp-000-summary.html | B | principles, identifiers, versioning, term-stability | RFC-2119-style normative principles (P1 open, P3 URI space, P4 versioning, P19 term stability, ...) — the audit checklist source for stable identifiers + life-cycle. Seed. |
| M2 | Jackson et al., "OBO Foundry in 2021: operationalizing open data principles to evaluate ontologies" (Database, baab069, 2021; PMID 34697637) | academic.oup.com/database/article/doi/10.1093/database/baab069/6316073 | A | obo, oquare, orthogonality, audit | The working audit recipe: principles turned into scored evaluations incl. orthogonality; curl 403 = bot wall, live via Firecrawl (200). Seed. |
| M3 | Duque-Ramos et al., "OQuaRE: A SQuaRE-based approach for evaluating the quality of ontologies" (CRCS 2011) | research.manchester.ac.uk/en/publications/oquare-a-square-based-approach-for-evaluating-the-quality-of-onto | A | oquare, quality-model, metrics | The original OQuaRE quality model (characteristics → subcharacteristics → metrics, scores 1-5). Added. |
| M4 | Duque-Ramos et al., "Evaluating the GoodOD Guideline with OQuaRE" (PLOS ONE, PMC4141745, 2016) | pmc.ncbi.nlm.nih.gov/articles/PMC4141745/ | A | oquare, application, competency-questions | Demonstrates OQuaRE alongside competency-question-based metrics — precedent for pairing OQuaRE scores with our gold-set F1. Added. |
| M5 | Quesada-Martínez et al., OBO Foundry ontologies evolution with OQuaRE (2017, PMID 28423828) | pubmed.ncbi.nlm.nih.gov/28423828/ | A | oquare, obo, evolution | OQuaRE applied across OBO versions — the longitudinal (version-over-version) audit pattern for config/ontology.yaml. Added. |
| M6 | OQuaRE tool (tecnomod-um/oquare, official implementation) | github.com/tecnomod-um/oquare | B | implementation, oquare | Reference implementation with score scaling 1-5 documented in the README. Added. |
| M7 | "Harmonizing the OQuaRE Quality Framework" | pdfs.semanticscholar.org/8f62/a6181787243c6de076c0eab6f871c603889b.pdf | B | oquare, caveat, metric-drift | Documents that OQuaRE metric definitions drifted across papers — pins which definition to use (TMOnto = mean parents per class, older definition). Added. |
| M8 | Guarino & Welty, "An Overview of OntoClean" (Handbook on Ontologies, Springer, ch. 8; DOI 10.1007/978-3-540-24750-0_8) | link.springer.com/chapter/10.1007/978-3-540-24750-0_8 · loa.istc.cnr.it/old/Papers/GuarinoWeltyOntoCleanv3.pdf | A | onto-clean, taxonomy, identity-unity | Metaproperty audit (rigidity, identity, unity) for the backbone taxonomy — catches subsumption misuse that F1 cannot see. Added. |

---

## Method validation — recommendations for the D13 wave (formula + caveats)

### R1. Benchmark recipe (layer 1)
- **Primary metric:** strict typed-span exact match, per entity and relation type: P_t, R_t, F1_t with 95% bootstrap CI. Resample **documents** (or paragraph groups for relations), not items; re-aggregate TP/FP/FN per resample; B ≥ 1,000 (practice runs 1,000-10,000: B7, B8, B9). Improvement = non-overlapping 95% CIs + Holm correction across the type family (B9 precedent; repo calibrate precedent).
- **Secondary metric:** one relaxed tier, pinned in config. Defensible conventions from graded sources: partial span overlap ≥ 50% of the shorter span (S10 practice) or embedding-similarity ≥ 0.95 per triple (B2). Graph-level similarity for ontology candidates: copy OLLM (B3) semantic+structural similarity; report all four SCION tiers (B1) so results stay comparable to the benchmark literature.
- **Caveat:** strict/relaxed choice moves F1 by several points (B1-B4 all show this); pin the rule before any A/B claim. Gold ladder stays D14: synthetic → hybrid silver → verified seeds.

### R2. Saturation recipe (layer 2) — validated, with owned assumptions
- **Map:** entity/relation type = "species"; chunk/document = sampling unit (incidence data → Chao2/ICE and incidence-based iNEXT curves, S1/S2/S3); density floor per chunk class = **sample-coverage floor** (C-hat ≥ 0.9 start, tighten to 0.95 once curves are stable; S13/S2).
- **Saturation verdict rule:** a project is "saturated" when the **coverage-based** R/E curve plateaus (Chao & Jost 2012 definition), NOT when raw type counts plateau. Raw counts keep growing forever in natural text (Heaps; S4/S5) — a count plateau is unreachable and must never be the gate.
- **Capture-recapture recall (extractor + verifier as two observers): applicable as a bounded recall estimate, with three pinned conditions** — (1) observers must be heterogeneous (different model families or extractor vs deterministic rules; same-family pairs underestimate the total by ~22%, cross-family ~10% low: S10; correlated errors within families are peer-reviewed: S9); (2) prefer ≥3-4 observers (software-inspection evidence: <4 inspectors → models not accurate enough, S8); (3) run a ~10% hand audit to correct for false positives and report the estimate as a **floor**, not recall (S10). Use the Chapman form of Lincoln-Petersen for two observers; jackknife-k for k ≥ 3 (S1 gives the jackknife formulas; S7 the theory).
- **Owned assumptions (no canonical NLP source exists for this adaptation — stated, not hidden):** closed-population within a snapshot window (violated by growing corpora → freeze the type universe per window); catchability heterogeneity (this is what Chao estimators are built for, S6); observer independence (enforced by construction via heterogeneous observers, audited via S9-style correlation checks); stable type definitions across windows (schema drift breaks curves — versioned schemas, P4 pillar).

### R3. Compression recipe (layer 3) + pinned loss gate
- **Selection:** MDL over ontology candidates, L(H)+L(D|H) (C1/SUBDUE form: compressed input size; C2/SSumM for a bit-budget knob; C6 for the principle citation).
- **Reduction pipeline (each step is graded-source-backed):** merge near-equivalent concepts via FCA contexts (C8/FCA-Map) → drop lattice-redundant type columns via FCA attribute reduction (C7) → transitive reduction on the DAG (C4, canonical O(n·m) DP) → prune zero-yield types detected as anomalous/unused by MDL soft rules (C3) → optionally publish a "slim" projection while keeping the full ontology addressable (C9 practice).
- **Pinned starting value (our convention — no paper pins delta-F1):** ship a compression only if held-out gold F1 drops **≤ 1.0 absolute point** AND the 95% bootstrap CIs of pre/post overlap. The CI-overlap clause is the binding check; the 1-point figure is conservative because measured CI half-widths at realistic gold-set sizes (300-1,500 items; B7/B8/S12) run 2-4 points, so sub-point changes are inside noise. Re-pin only with evidence from our own CI measurements. Confidence: strong inference, not established fact.

### R4. MECE + future-proofing audit recipe (config/ontology.yaml)
1. **Disjointness coverage:** every entity-type pair is either disjoint or carries an explicit relation; OWL disjointness axioms in the shipped ontology; orthogonality scoring per M2.
2. **FCA structure check:** build the formal context (instances × types); run attribute reduction (C7) to flag reducible types; run FCA-Map-style contexts (C8) against a reference vocabulary to surface merge candidates.
3. **OQuaRE scores (1-5)** for the ontology file: structural characteristic (cohesion, tangledness = mean parents per class, redundancy) per M3/M6, using the pinned older metric definitions (M7). Pair scores with competency-question checks (M4).
4. **OntoClean metaproperties** on the backbone taxonomy: rigidity, identity, unity (M8) — catches subsumption errors that per-type F1 cannot see.
5. **Life-cycle:** stable identifiers (URIs), versioned schemas, term stability policy, RFC-2119 conformance checklist (M1); longitudinal OQuaRE diff per release (M5).
- **Caveat:** OQuaRE metric definitions drifted across its literature (M7); pin our definition set in the repo before first audit run, or scores are not comparable across releases.

## D14 — Prompt versioning mechanism (≤3 candidates, one recommendation)

| Candidate | Verified? | Trade-offs |
|---|---|---|
| (1) promptfoo-style self-hosted CLI (declarative YAML cases, providers incl. local Ollama, programmatic assertions, diff/cache views, CI-friendly) | docs live 2026-11-25 (promptfoo.dev/docs/intro, 200) | Adds a Node dev-dep (repo already runs pnpm for ui_src, so no new runtime class); external tool ownership; fits zero-drift rule 10 (`poe prompt-eval` + CI job + map row). |
| (2) Repo-native pytest regression harness (fixture corpus + extraction assertions + baseline JSON) | repo pattern (scripts/quality-baselines.json ratchet lineage) | No new dep; but re-implements eval matrix, provider adapters, and diff views; maintenance cost lands on us. |
| (3) Hosted/observability platforms (LangSmith, Langfuse) | not fetched this run | Fast start, dashboards free; data leaves repo, per-call cost, and gate integration violates the repo's self-hosted CI pattern. Rejected. |

**Recommendation: (1) promptfoo-style, self-hosted.** Prompt files live in-repo with semver front-matter + changelog; each eval run pins prompt version + corpus hash + scores into an append-only results file (mirrors the quality-baselines ratchet); wired as `poe prompt-eval` with a CI job + enforcement-map row per rule 10. Fallback to (2) only if the Node dep is blocked. Measured improvement claims then cite the pinned eval corpus, satisfying "evidence-based prompt changes only" (D14).

## Honest gaps / UNKNOWN
- No canonical NLP source exists for the Chao/iNEXT + Heaps saturation adaptation; the piece citations above are the strongest defensible base. Owner assumption statement required in any spec that cites it.
- No paper pins a downstream delta-F1 compression gate; R3's pinned value is our convention with stated rationale.
- Rissanen 1978 and Heaps 1978 entered the registry at citation-grade (canonical, not fetched); verify links before any gate code or docs PR quotes them.
- chao.site/python/iNEXT/ (seed) is DEAD — replaced by S3 (three live links).
