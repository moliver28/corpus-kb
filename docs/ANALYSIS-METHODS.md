# Analysis methods (the register)

Corpus-KB ships a register of 30 NLP analysis methods. The register
(`config/analysis_methods.yaml`) is the single source. It drives the
selector (`analysis_select_method`), the generated table below, and the
harness method cards. This file is part of that contract: the block
between the markers is GENERATED — run `poe gen` after any register
edit, never hand-edit the block.

## How to pick a method

Walk the axes in order. Stop at the first method that answers the
question within your budget.

1. State the research question in one sentence.
2. Name the output family the question needs: labels, themes, scores,
   structures, text, errors, or recommendations.
3. List the inputs you actually have: raw text, embeddings, labels,
   seed labels, predictions, timestamps, an ontology, or an LLM.
4. Budget the calibration effort (none, low, moderate, high) and the
   autonomy level (automated with audit, human in the loop, human led).
5. Read the robustness caveat and the bias risk for the candidate.
6. Run the op. Then do the validation step it names. Claim only what
   its `permitted_claims` line allows.

`analysis_select_method` applies this walk for you. It ranks the
register with a fixed rubric: output family match, input coverage,
complexity tier, calibration effort, selection priority, then id. The
rubric is deterministic and never calls a model.

The four selection rules behind the register:

1. Match the required output.
2. Prefer direct, inspectable evidence over inferred constructs.
3. Prefer the method that needs fewer unverified assumptions.
4. Use a more complex method only after the simpler baseline fails a
   defined evaluation.

Top2Vec, KeyLLM, and MMR appear only in the source document's
footnotes. They have no register rows and no ops.

## Adding a method

1. Add one row to `config/analysis_methods.yaml` (and its packaged
   mirror, byte-identical). The loader validates every axis, the id
   ledger 01-30, and the status/op consistency.
2. Give it an `op` name only when it is implemented. `human_led` and
   `not_planned` rows carry `op: null`.
3. To implement an op: add the handler under `src/corpus_kb/tools/`
   (the house shape: pool, config, tenant, typed errors, honest
   envelopes), then the catalogue row in
   `src/corpus_kb/contracts/catalogue_analysis.py`, the parity leg in
   `tests/test_surface_parity_deep.py`, the registry row and card in
   `src/corpus_kb/surface_registry.py`, and the thin CLI, HTTP, and
   socket adapters. Run `poe gen`. The gates refuse a partial wiring.
4. Run `poe gen` again; this page's table updates from the YAML.

<!-- amr-register:generated:start -->
Register version: 1.0.0. Source: I want to understand the NLP Analysis landscape so that I can ensure a team of non-NLP researchers are selected methods appropriately when using an NLP harness around a claude code like tool.

| Id | Method | Output family | Inputs | Calibration | Autonomy | Complexity | Status | Op |
|----|--------|---------------|--------|-------------|----------|------------|--------|----|
| 01 | Full manual codebook analysis (baseline) | themes | raw_text | high | human_led | advanced | human-led | (none) |
| 02 | Rapid Qualitative Analysis (RQA) matrix | structures | raw_text | moderate | human_in_loop | baseline | existing op | analyze_run |
| 03 | Framework Method | structures | raw_text | high | human_in_loop | advanced | existing op | analyze_run |
| 04 | Dictionary or rule-based classification | labels | raw_text | low | automated_with_audit | baseline | existing op | labeling_build |
| 05 | TF-IDF keyphrase scan | themes | raw_text | low | automated_with_audit | baseline | new op | keyphrases_extract |
| 06 | Embedding-based keyphrase extraction (KeyBERT) | themes | raw_text, embeddings | moderate | automated_with_audit | standard | new op | keyphrases_extract |
| 07 | Statistical keyphrase extraction (YAKE) | themes | raw_text | low | automated_with_audit | baseline | new op | keyphrases_extract |
| 08 | Classical topic modeling (NMF or LDA) | themes | raw_text | moderate | automated_with_audit | standard | not planned | (none) |
| 09 | Unsupervised BERTopic | themes | raw_text, embeddings | moderate | automated_with_audit | standard | existing op | topics_build |
| 10 | Guided BERTopic | themes | raw_text, embeddings, seed_labels | moderate | human_in_loop | standard | new op | topics_build |
| 11 | Zero-shot BERTopic | themes | raw_text, embeddings, labels | moderate | human_in_loop | standard | new op | topics_build |
| 12 | Embedding clustering (including interactive clustering) | structures | raw_text, embeddings | moderate | human_in_loop | standard | new op | clusters_inspect |
| 13 | Embedding-centroid classification | labels | embeddings, labels | low | automated_with_audit | baseline | new op | centroids_classify |
| 14 | Nearest-neighbor classification | labels | embeddings, labels | low | automated_with_audit | baseline | new op | neighbors_classify |
| 15 | TF-IDF with logistic regression or linear SVM | labels | raw_text, labels | moderate | automated_with_audit | baseline | new op | lexical_classify |
| 16 | Frozen embeddings with a linear classifier | labels | embeddings, labels | moderate | automated_with_audit | standard | existing op | classification_build |
| 17 | SetFit few-shot classification | labels | raw_text, labels | moderate | automated_with_audit | advanced | existing op | classification_build |
| 18 | Zero-shot NLI classification | labels | raw_text, llm | moderate | automated_with_audit | standard | new op | nli_classify |
| 19 | Programmatic weak supervision (Snorkel) | labels | raw_text, predictions | high | human_in_loop | advanced | existing op | labeling_build |
| 20 | Active learning loop | recommendations | labels, predictions | high | human_in_loop | advanced | new op | active_learn_next |
| 21 | Human-in-the-loop computational grounded theory | themes | raw_text | high | human_led | advanced | human-led | (none) |
| 22 | Extract, embed, cluster, summarize (EECS) | text | raw_text, embeddings | high | human_in_loop | advanced | new op | analyze_run |
| 23 | Hybrid retrieval with reranking | text | raw_text, embeddings, llm | moderate | automated_with_audit | standard | existing op | search |
| 24 | Extractive question answering | text | raw_text, llm | moderate | automated_with_audit | standard | existing op | ask |
| 25 | Named entity recognition (including GLiNER) | structures | raw_text, llm | moderate | automated_with_audit | standard | existing op | ingest_text |
| 26 | Sentiment or emotion classification | scores | raw_text | moderate | automated_with_audit | baseline | new op | sentiment_screen |
| 27 | Aspect-based sentiment analysis | labels | raw_text, labels | high | human_in_loop | standard | new op | sentiment_screen |
| 28 | NLI-based contradiction screening | errors | raw_text, llm, timestamps | moderate | human_in_loop | standard | new op | contradictions_screen |
| 29 | Near-duplicate and paraphrase detection | errors | raw_text, embeddings | moderate | automated_with_audit | baseline | new op | duplicates_screen |
| 30 | Outlier or novelty detection | errors | raw_text, embeddings | moderate | human_in_loop | baseline | new op | outliers_rank |

## Method guidance

- **01 — Full manual codebook analysis (baseline)**. Use it to support detailed interpretation and an auditable record of code application.
  - Validate: Check code application and analytical adequacy under the chosen approach. Human analysis is not error-free.
  - Claim only: Interpreted claims, with the codebook version and analyst decisions recorded.
  - Do not claim: Do not claim human coding is an error-free reference standard.
  - Cautions: Coding procedures can drift as analysts meet new cases. Bias: Researcher expectations, selective attention, and premature code closure can shape the result.
- **02 — Rapid Qualitative Analysis (RQA) matrix** (op: `analyze_run`). Answer a bounded set of questions. Read the original evidence before consequential decisions.
  - Validate: Pilot the template. Have another analyst inspect the summaries. Revise missing or ambiguous fields.
  - Claim only: Bounded answers for the templated questions, with links to evidence.
  - Do not claim: Do not claim the matrix equals a full line-by-line analysis.
  - Cautions: Summary templates can omit detail that later analysis needs. Bias: Template and interview-guide framing can compress the summaries.
- **03 — Framework Method** (op: `analyze_run`). Compare cases, groups, or conditions. Keep access to the case context.
  - Validate: Set categories and charting rules. Check a pilot matrix against the source material.
  - Claim only: Case comparisons that preserve cited context.
  - Do not claim: Do not claim the charting preserved every narrative detail.
  - Cautions: Matrix reduction can weaken narrative and sequence information. Bias: Fixed framework categories can make uncategorized material less visible.
- **04 — Dictionary or rule-based classification** (op: `labeling_build`). Find explicit mentions and build a review queue.
  - Validate: Inspect matches and non-matches. Add exclusions. Test the rules on independent records.
  - Claim only: Counts of rule matches, unless broader validity is established.
  - Do not claim: Do not claim rule matches prove implicit meaning.
  - Cautions: Matches are sensitive to synonyms, negation, spelling, and context. Bias: Vocabulary choices can favor some groups or ways of speaking.
- **05 — TF-IDF keyphrase scan** (op: `keyphrases_extract`). Inspect corpus vocabulary, find search terms, and improve retrieval.
  - Validate: Review tokenization, common words, phrase lengths, and the top-ranked terms.
  - Claim only: Descriptions of distinctive wording in the corpus.
  - Do not claim: Do not call the ranked list themes.
  - Cautions: Sensitive to document boundaries, vocabulary filtering, and repeated boilerplate. Bias: Rare wording can rank highly without research importance.
- **06 — Embedding-based keyphrase extraction (KeyBERT)** (op: `keyphrases_extract`). Create document previews, search suggestions, and candidate descriptors.
  - Validate: Review candidate generation, model choice, diversity settings, and an output sample.
  - Claim only: Phrases similar in meaning to the document, by embedding similarity.
  - Do not claim: Do not read similarity scores as probabilities of analytical importance.
  - Cautions: Sensitive to the embedding model, candidate generation, and document length. Bias: The representation can favor language covered in model training.
- **07 — Statistical keyphrase extraction (YAKE)** (op: `keyphrases_extract`). Get a quick lexical overview and compare it with embedding-based extraction.
  - Validate: Review language and filtering settings. Inspect short and long texts separately.
  - Claim only: Statistically salient phrases for the supplied texts.
  - Do not claim: Do not claim research importance for high-scoring phrases.
  - Cautions: Short texts and filtering choices can change the results. Bias: Surface salience can miss implicit or uncommon concepts.
- **08 — Classical topic modeling (NMF or LDA)**. Navigate a corpus and form questions for close reading.
  - Validate: Compare preprocessing and topic counts. Inspect representative and poorly fitting records.
  - Claim only: Exploratory topic descriptions for navigation.
  - Do not claim: Do not read a topic weight as proof of a qualitative theme.
  - Cautions: Topic results change with preprocessing, topic count, and fitting choices. Bias: Frequent vocabulary and analyst-selected topic counts shape the results.
- **09 — Unsupervised BERTopic** (op: `topics_build`). Build an exploratory corpus map and sample records for inspection.
  - Validate: Review segmentation and clustering settings. Inspect clusters and outliers. Repeat with controlled changes.
  - Claim only: Exploratory corpus maps with cluster descriptions.
  - Do not claim: Do not present a topic representation as a validated research theme.
  - Cautions: Clusters depend on embeddings, dimensionality reduction, and clustering settings. Bias: Small or dispersed groups can be treated as outliers.
- **10 — Guided BERTopic** (op: `topics_build`). Explore expected subject areas while checking material outside them.
  - Validate: Review seed coverage and alternatives. Compare against an unguided run.
  - Claim only: Topics shaped by declared seed concepts.
  - Do not claim: Do not claim the seeds produced complete or unbiased discovery.
  - Cautions: Seeds do not guarantee the wanted topics or complete discovery. Bias: Supplied topic vocabulary can create confirmation bias.
- **11 — Zero-shot BERTopic** (op: `topics_build`). Combine known-topic routing with an explicit route for unexpected material.
  - Validate: Test descriptions and thresholds. Inspect assigned and unassigned text.
  - Claim only: Assignments to supplied topics, with the threshold recorded.
  - Do not claim: Do not equate this similarity matching with NLI classification.
  - Cautions: Description wording and thresholds affect assignment versus discovery. Bias: Familiar topics can absorb material that merits a different reading.
- **12 — Embedding clustering (including interactive clustering)** (op: `clusters_inspect`). Organize review and compare alternative group structures.
  - Validate: Compare representations and grouping settings. Review boundary cases. Use feedback if available.
  - Claim only: Exploratory groups with examples and distances.
  - Do not claim: Do not claim semantic similarity equals the similarity the research question needs.
  - Cautions: A stable-looking group can reflect topic, style, speaker, or document format. Bias: Majority patterns and embedding training data shape the group structure.
- **13 — Embedding-centroid classification** (op: `centroids_classify`). Apply a small set of concrete categories with inspectable prototypes.
  - Validate: Inspect the examples. Measure category precision, recall, and abstention. Tune thresholds on separate data.
  - Claim only: Category assignments with similarities and recorded abstentions.
  - Do not claim: Do not claim one centroid represents every form of a category.
  - Cautions: One mean can hide distinct subgroups inside a category. Bias: Example selection defines the category prototype.
- **14 — Nearest-neighbor classification** (op: `neighbors_classify`). Support example-based review and find missing training examples.
  - Validate: Review example coverage. Tune the neighbor count and rejection rules.
  - Claim only: Category assignments backed by listed neighbor examples.
  - Do not claim: Do not claim coverage the labeled examples do not have.
  - Cautions: Sensitive to neighbor coverage, distance choice, and local label errors. Bias: Overrepresented examples can dominate nearby votes.
- **15 — TF-IDF with logistic regression or linear SVM** (op: `lexical_classify`). Set a reproducible classification baseline before a more expensive model.
  - Validate: Fit the baseline. Inspect errors. Tune features without the final test set.
  - Claim only: Baseline performance on independent test records.
  - Do not claim: Do not claim transfer to shifted vocabulary without a test.
  - Cautions: Vocabulary shift and shortcut words can reduce transfer. Bias: Labels can attach to names, groups, or formatting instead of the concept.
- **16 — Frozen embeddings with a linear classifier** (op: `classification_build`). Run bounded classification when the frozen representation preserves the categories.
  - Validate: Measure category-level performance on an evaluation set. Audit errors.
  - Claim only: Category-level results tied to the recorded embedding-model version.
  - Do not claim: Do not claim the encoder preserves distinctions it was never shown.
  - Cautions: A fixed encoder cannot learn distinctions it does not represent. Bias: Training examples and pretrained representations both shape the decisions.
- **17 — SetFit few-shot classification** (op: `classification_build`). Improve a defined classification task when labeled examples are available.
  - Validate: Compare with frozen embeddings and a lexical baseline. Review errors.
  - Claim only: Few-shot results for the tested categories and example set.
  - Do not claim: Do not promise a published benchmark number for a new corpus.
  - Cautions: Few examples may not cover category diversity. Noisy examples harm adaptation. Bias: Small training sets can amplify analyst-selected examples.
- **18 — Zero-shot NLI classification** (op: `nli_classify`). Triage records or propose labels before any labeled data exists.
  - Validate: Test paraphrased descriptions, negation, and overlapping labels. Tune thresholds on labeled data.
  - Claim only: Pilot label proposals with support scores.
  - Do not claim: Do not treat zero-shot scores as established probabilities.
  - Cautions: Wording, lexical overlap, and syntax can produce wrong support judgments. Bias: Label descriptions and training-domain assumptions shape the results.
- **19 — Programmatic weak supervision (Snorkel)** (op: `labeling_build`). Turn reusable domain rules into training data. Keep an independent human evaluation set.
  - Validate: Inspect the labeling functions. Measure conflicts and coverage. Evaluate on independent labels.
  - Claim only: Training labels with coverage and conflict reports.
  - Do not claim: Do not claim label quality without an independent evaluation set.
  - Cautions: Correlated rules can repeat the same error. Low coverage stays a problem. Bias: Rule authors can encode the same assumptions in several functions.
- **20 — Active learning loop** (op: `active_learn_next`). Reduce annotation effort for a defined task. Keep the final evaluation sample separate.
  - Validate: Assess improvement per annotation effort. Compare against random sampling.
  - Claim only: Annotation-order recommendations with the strategy recorded.
  - Do not claim: Do not claim the selection strategy always beats random sampling.
  - Cautions: Uncertainty sampling can chase difficult or noisy cases, not representative ones. Bias: Selected annotation data need not match corpus prevalence.
- **21 — Human-in-the-loop computational grounded theory**. Develop and challenge an explanation with the corpus.
  - Validate: Test the explanation against close reading, alternative readings, and further evidence.
  - Claim only: An interpreted account that survived stated tests.
  - Do not claim: Do not present it as autonomous theme discovery.
  - Cautions: Computational patterns do not remove the need for contextual interpretation. Bias: Pattern selection and theoretical interpretation can reinforce prior beliefs.
- **22 — Extract, embed, cluster, summarize (EECS)** (op: `analyze_run`). Produce a structured discovery aid. Audit extraction omissions before reading the groups.
  - Validate: Evaluate extraction coverage, grouping quality, and summary fidelity separately.
  - Claim only: A structured discovery aid with per-stage audit results.
  - Do not claim: Do not claim end-to-end accuracy from the components' evidence.
  - Cautions: Early extraction omissions cannot be repaired by later clustering or summaries. Bias: Extraction rules, grouping, and summary selection can each drop minority evidence.
- **23 — Hybrid retrieval with reranking** (op: `search`). Assemble evidence for a question and inspect counter-evidence.
  - Validate: Check known relevant passages, missed evidence, and question variants.
  - Claim only: Evidence lists for stated questions, with scores by type.
  - Do not claim: Do not read retrieval scores as answer truth.
  - Cautions: Performance can change across retrieval tasks and domains. Bias: Questions and ranking models decide which evidence becomes visible.
- **24 — Extractive question answering** (op: `ask`). Fill narrow evidence fields where the answer is stated in the source.
  - Validate: Test representative questions. Tune no-answer handling. Inspect unsupported spans.
  - Claim only: Stated-answer spans with source locations.
  - Do not claim: Do not claim answers to questions the passages do not answer.
  - Cautions: A plausible span can be wrong or unsupported. No-answer behavior matters. Bias: Question wording and passage selection constrain the possible answers.
- **25 — Named entity recognition (including GLiNER)** (op: `ingest_text`). Create inventories and filters, or prepare data for entity resolution.
  - Validate: Define types and boundary rules. Label a span-level test set. Tune the model or thresholds.
  - Claim only: Span inventories for the declared entity types.
  - Do not claim: Do not assume two matching names are the same entity.
  - Cautions: Entity boundaries, unfamiliar terms, and domain shift can reduce performance. Bias: Type definitions and training data can favor familiar names or entity forms.
- **26 — Sentiment or emotion classification** (op: `sentiment_screen`). Review explicit evaluative language under a defined construct.
  - Validate: Define the construct. Label and test each category, including neutral or uncertain cases.
  - Claim only: Screening flags for evaluative language under the defined construct.
  - Do not claim: Do not infer a participant's internal state without further evidence.
  - Cautions: Mixed feelings, indirect language, and code-mixed language need task-specific tests. Bias: Expression style can be mistaken for an emotional state.
- **27 — Aspect-based sentiment analysis** (op: `sentiment_screen`). Compare evaluations of defined product or service attributes.
  - Validate: Evaluate aspect extraction and target-linked sentiment separately, then jointly.
  - Claim only: Aspect-linked sentiment for the declared aspects.
  - Do not claim: Do not claim opinions on aspects the definitions never covered.
  - Cautions: Correct sentiment requires the correct target and aspect link. Bias: Predefined aspects can exclude concerns outside the team's framework.
- **28 — NLI-based contradiction screening** (op: `contradictions_screen`). Build a contradiction-review queue for human reviewers.
  - Validate: Test negation, entity identity, time, conditions, and statements that merely differ.
  - Claim only: Candidate contradictions for human adjudication.
  - Do not claim: Do not claim a model decision proves a claim false in the world.
  - Cautions: Different times or conditions can make apparent contradictions compatible. Bias: Candidate selection can favor disagreement and miss agreement or context.
- **29 — Near-duplicate and paraphrase detection** (op: `duplicates_screen`). Remove duplicate ingestion and find repeated material. Preserve independent participant records.
  - Validate: Measure pairwise precision and recall at the action threshold. Label candidate pairs.
  - Claim only: Duplicate candidates at a recorded threshold, for human approval.
  - Do not claim: Do not merge records without human approval of the merge policy.
  - Cautions: Similar meaning does not establish interchangeable evidence. Bias: Merging can erase independent speakers or minority wording.
- **30 — Outlier or novelty detection** (op: `outliers_rank`). Find exceptions, data problems, and possibly overlooked experiences.
  - Validate: Define the reference group. Inspect flagged records. Compare detectors and representations.
  - Claim only: A ranked review list with the detector and reference recorded.
  - Do not claim: Do not equate unusualness with importance.
  - Cautions: Results depend on the representation and the definition of normality. Bias: Minority language, rare experiences, or accessibility needs can look anomalous.
<!-- amr-register:generated:end -->
