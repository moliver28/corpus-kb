# Analysis methods (the register)

Corpus-KB ships a register of 30 NLP analysis methods. The register
(`config/analysis_methods.yaml`) is the single source. It drives the
selector (`analysis_select_method`), the generated table below, and the
harness method cards. The block between the markers is GENERATED. Run
`poe gen` after any register edit. Never hand-edit the block.

## How to pick a method

Walk the axes in order. Stop at the first method that answers the
question within your budget.

1. State the research question in one sentence.
2. Name the output family the question needs: labels, themes, scores,
   structures, text, errors, or recommendations.
3. List the inputs you actually have: raw text, embeddings, labels,
   seed labels, predictions, timestamps, an ontology, or an LLM.
4. Budget the calibration effort: none, low, moderate, or high. Then
   budget the autonomy: automated with audit, human in the loop, or
   human led.
5. Read the robustness caveat and the bias risk for the candidate.
6. Run the op. Then do the validation step it names. Claim only what
   its `permitted_claims` line allows.

`analysis_select_method` applies this walk for you. It ranks the
register with a fixed rubric. The order: output family match, input
coverage, complexity tier, calibration effort, selection priority,
then id. The rubric is deterministic and never calls a model.

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
3. To implement an op, add the handler under `src/corpus_kb/tools/`.
   Use the house shape: pool, config, tenant, typed errors, honest
   envelopes. Then add the catalogue row in
   `src/corpus_kb/contracts/catalogue_analysis.py`. Add the parity leg
   in `tests/test_surface_parity_deep.py`. Add the registry row and
   card in `src/corpus_kb/surface_registry.py`. Finish with the thin
   CLI, HTTP, and socket adapters. Run `poe gen`. The gates refuse a
   partial wiring.
4. Run `poe gen` again; this page's table updates from the YAML.

<!-- amr-register:generated:start -->
Register version: 1.1.0. Source: the research document named in `config/analysis_methods.yaml`.

| Id | Method | Output family | Inputs | Calibration | Autonomy | Complexity | Status | Op |
|----|--------|---------------|--------|-------------|----------|------------|--------|----|
| 01 | Full manual codebook analysis (baseline) | `themes` | `raw_text` | `high` | `human_led` | `advanced` | human-led | (none) |
| 02 | Rapid Qualitative Analysis (RQA) matrix | `structures` | `raw_text` | `moderate` | `human_in_loop` | `baseline` | existing op | `analyze_run` |
| 03 | Framework Method | `structures` | `raw_text` | `high` | `human_in_loop` | `advanced` | existing op | `analyze_run` |
| 04 | Dictionary or rule-based classification | `labels` | `raw_text` | `low` | `automated_with_audit` | `baseline` | existing op | `labeling_build` |
| 05 | TF-IDF keyphrase scan | `themes` | `raw_text` | `low` | `automated_with_audit` | `baseline` | new op | `keyphrases_extract` |
| 06 | Embedding-based keyphrase extraction (KeyBERT) | `themes` | `raw_text`, `embeddings` | `moderate` | `automated_with_audit` | `standard` | new op | `keyphrases_extract` |
| 07 | Statistical keyphrase extraction (YAKE) | `themes` | `raw_text` | `low` | `automated_with_audit` | `baseline` | new op | `keyphrases_extract` |
| 08 | Classical topic modeling (NMF or LDA) | `themes` | `raw_text` | `moderate` | `automated_with_audit` | `standard` | not planned | (none) |
| 09 | Unsupervised BERTopic | `themes` | `raw_text`, `embeddings` | `moderate` | `automated_with_audit` | `standard` | existing op | `topics_build` |
| 10 | Guided BERTopic | `themes` | `raw_text`, `embeddings`, `seed_labels` | `moderate` | `human_in_loop` | `standard` | new op | `topics_build` |
| 11 | Zero-shot BERTopic | `themes` | `raw_text`, `embeddings`, `labels` | `moderate` | `human_in_loop` | `standard` | new op | `topics_build` |
| 12 | Embedding clustering (including interactive clustering) | `structures` | `raw_text`, `embeddings` | `moderate` | `human_in_loop` | `standard` | new op | `clusters_inspect` |
| 13 | Embedding-centroid classification | `labels` | `embeddings`, `labels` | `low` | `automated_with_audit` | `baseline` | new op | `centroids_classify` |
| 14 | Nearest-neighbor classification | `labels` | `embeddings`, `labels` | `low` | `automated_with_audit` | `baseline` | new op | `neighbors_classify` |
| 15 | TF-IDF with logistic regression or linear SVM | `labels` | `raw_text`, `labels` | `moderate` | `automated_with_audit` | `baseline` | new op | `lexical_classify` |
| 16 | Frozen embeddings with a linear classifier | `labels` | `embeddings`, `labels` | `moderate` | `automated_with_audit` | `standard` | existing op | `classification_build` |
| 17 | SetFit few-shot classification | `labels` | `raw_text`, `labels` | `moderate` | `automated_with_audit` | `advanced` | existing op | `classification_build` |
| 18 | Zero-shot NLI classification | `labels` | `raw_text`, LLM | `moderate` | `automated_with_audit` | `standard` | new op | `nli_classify` |
| 19 | Programmatic weak supervision (Snorkel) | `labels` | `raw_text`, `predictions` | `high` | `human_in_loop` | `advanced` | existing op | `labeling_build` |
| 20 | Active learning loop | `recommendations` | `labels`, `predictions` | `high` | `human_in_loop` | `advanced` | new op | `active_learn_next` |
| 21 | Human-in-the-loop computational grounded theory | `themes` | `raw_text` | `high` | `human_led` | `advanced` | human-led | (none) |
| 22 | Extract, embed, cluster, summarize (EECS) | `text` | `raw_text`, `embeddings` | `high` | `human_in_loop` | `advanced` | new op | `analyze_run` |
| 23 | Hybrid retrieval with reranking | `text` | `raw_text`, `embeddings`, LLM | `moderate` | `automated_with_audit` | `standard` | existing op | `search` |
| 24 | Extractive question answering | `text` | `raw_text`, LLM | `moderate` | `automated_with_audit` | `standard` | existing op | `ask` |
| 25 | Named entity recognition (including GLiNER) | `structures` | `raw_text`, LLM | `moderate` | `automated_with_audit` | `standard` | existing op | `ingest_text` |
| 26 | Sentiment or emotion classification | `scores` | `raw_text` | `moderate` | `automated_with_audit` | `baseline` | new op | `sentiment_screen` |
| 27 | Aspect-based sentiment analysis | `labels` | `raw_text`, `labels` | `high` | `human_in_loop` | `standard` | new op | `sentiment_screen` |
| 28 | NLI-based contradiction screening | `errors` | `raw_text`, LLM, `timestamps` | `moderate` | `human_in_loop` | `standard` | new op | `contradictions_screen` |
| 29 | Near-duplicate and paraphrase detection | `errors` | `raw_text`, `embeddings` | `moderate` | `automated_with_audit` | `baseline` | new op | `duplicates_screen` |
| 30 | Outlier or novelty detection | `errors` | `raw_text`, `embeddings` | `moderate` | `human_in_loop` | `baseline` | new op | `outliers_rank` |

## Method guidance

- **01 — Full manual codebook analysis (baseline)**. Use it to support detailed interpretation and an auditable record of code application.
  - Validate: Check code application and analytical adequacy under the chosen approach. Human analysis is not error-free.
  - Claim only: Interpreted claims, with the codebook version and analyst decisions recorded.
  - Do not claim: Do not claim human coding is an error-free reference standard.
  - Cautions: Coding procedures can drift as analysts meet new cases. Bias: Researcher expectations, selective attention, and premature code closure can shape the result.
  - Routing axes: task `representation`, comparison `none`, output `narrative`, evidence `labels`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Codes must stay stable as analysts meet new cases; drift changes the reading.
  - Null check: None; the check is analyst agreement and an audit trail, not a statistic.
  - Watch for: Selective attention and premature code closure can hide cases that do not fit.
  - Limits: Manual close reading wins for interpretive claims.
  - Workload: Compute is minor; human reading hours set the practical ceiling.
  - Ask it: "How do I build a codebook for my interview study?" / "Can researchers hand code these transcripts step by step?" / "I want to define and apply my own codes carefully."
  - Follow-ups: "Which cases resist my current codes?" / "How do I record code changes over time?" / "Where do two codes overlap in one passage?"
  - Evidence: https://pmc.ncbi.nlm.nih.gov/articles/PMC11877799/ (Journal 2025)
- **02 — Rapid Qualitative Analysis (RQA) matrix** (op: `analyze_run`). Answer a bounded set of questions. Read the original evidence before consequential decisions.
  - Validate: Pilot the template. Have another analyst inspect the summaries. Revise missing or ambiguous fields.
  - Claim only: Bounded answers for the templated questions, with links to evidence.
  - Do not claim: Do not claim the matrix equals a full line-by-line analysis.
  - Cautions: Summary templates can omit detail that later analysis needs. Bias: Template and interview-guide framing can compress the summaries.
  - Routing axes: task `measurement`, comparison `subset_vs_subset`, output `point_answer`, evidence `claims`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: The template questions stay bounded; summaries can compress away needed detail.
  - Null check: None; a second analyst reviews the matrix instead of a statistic.
  - Watch for: Template framing can compress answers and hide detail later analysis needs.
  - Limits: Answers stay bounded; read the original evidence before big decisions.
  - Workload: Human summarization dominates the workload; compute is minor.
  - Ask it: "How do I compare interview cases on a fixed question set?" / "Show answers per case in a simple matrix view." / "Can I summarize each case against my study questions?"
  - Follow-ups: "Which cases disagree on this question?" / "Is the template missing an answer type?" / "Where do summaries need the original quote?"
  - Evidence: https://pmc.ncbi.nlm.nih.gov/articles/PMC11877799/ (Journal 2025)
- **03 — Framework Method** (op: `analyze_run`). Compare cases, groups, or conditions. Keep access to the case context.
  - Validate: Set categories and charting rules. Check a pilot matrix against the source material.
  - Claim only: Case comparisons that preserve cited context.
  - Do not claim: Do not claim the charting preserved every narrative detail.
  - Cautions: Matrix reduction can weaken narrative and sequence information. Bias: Fixed framework categories can make uncategorized material less visible.
  - Routing axes: task `measurement`, comparison `subset_vs_subset`, output `narrative`, evidence `labels`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Categories hold still while charting; uncategorized material stays visible.
  - Null check: None; a pilot matrix is checked against source material by hand.
  - Watch for: Matrix reduction can weaken narrative and sequence information.
  - Limits: Deep interpretation beats the matrix when narrative sequence matters.
  - Workload: Charting and interpretation hours dominate; compute is minor.
  - Ask it: "How do I chart coded evidence case by case?" / "I need a case by category matrix of my findings." / "Compare groups of participants across framework categories."
  - Follow-ups: "Which categories split the cases most cleanly?" / "What sits outside the framework columns?" / "How do two case groups compare on each category?"
  - Evidence: https://doi.org/10.1186/1471-2288-13-117 (Journal 2013)
- **04 — Dictionary or rule-based classification** (op: `labeling_build`). Find explicit mentions and build a review queue.
  - Validate: Inspect matches and non-matches. Add exclusions. Test the rules on independent records.
  - Claim only: Counts of rule matches, unless broader validity is established.
  - Do not claim: Do not claim rule matches prove implicit meaning.
  - Cautions: Matches are sensitive to synonyms, negation, spelling, and context. Bias: Vocabulary choices can favor some groups or ways of speaking.
  - Routing axes: task `measurement`, comparison `none`, output `point_answer`, evidence `lexical`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: The rule vocabulary covers the surface forms; synonyms and spelling stay handled.
  - Null check: None; sampled matches and non-matches are inspected by hand.
  - Watch for: Negation and context flip matches; counts never prove implicit meaning.
  - Limits: Rule matches find explicit mentions; implicit meaning needs reading or models.
  - Workload: One text scan over n records; no neural inference, fast at corpus scale.
  - Ask it: "Find every mention of these specific words or phrases." / "Can simple rules label records that name this issue?" / "I want pattern matching for known terms in the text."
  - Follow-ups: "Which rules over-match or under-match?" / "What do the unmatched records say?" / "Should an exclusion cover negated phrases?"
  - Evidence: https://docs.v1.argilla.io/en/v1.10.0/reference/webapp/features.html (Documentation)
- **05 — TF-IDF keyphrase scan** (op: `keyphrases_extract`). Inspect corpus vocabulary, find search terms, and improve retrieval.
  - Validate: Review tokenization, common words, phrase lengths, and the top-ranked terms.
  - Claim only: Descriptions of distinctive wording in the corpus.
  - Do not claim: Do not call the ranked list themes.
  - Cautions: Sensitive to document boundaries, vocabulary filtering, and repeated boilerplate. Bias: Rare wording can rank highly without research importance.
  - Routing axes: task `discovery`, comparison `none`, output `ranked_list`, evidence `lexical`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: Document boundaries and word filtering stay fixed; boilerplate stays identified.
  - Null check: None; the top-ranked terms are reviewed by hand before use.
  - Watch for: Repeated boilerplate and rare words can outrank meaningful terms.
  - Limits: Distinctive wording only; themes need reading or topic models.
  - Workload: One sparse matrix build and ranking over n units; CPU only.
  - Ask it: "What words come up most often across these documents?" / "Which terms stand out as unusual in this corpus?" / "Show the most distinctive wording in the collection."
  - Follow-ups: "Which top terms look meaningless here?" / "What rare words rank surprisingly high?" / "How does filtering change the ranked list?"
  - Evidence: https://scikit-learn.org/1.5/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html (Documentation)
- **06 — Embedding-based keyphrase extraction (KeyBERT)** (op: `keyphrases_extract`). Create document previews, search suggestions, and candidate descriptors.
  - Validate: Review candidate generation, model choice, diversity settings, and an output sample.
  - Claim only: Phrases similar in meaning to the document, by embedding similarity.
  - Do not claim: Do not read similarity scores as probabilities of analytical importance.
  - Cautions: Sensitive to the embedding model, candidate generation, and document length. Bias: The representation can favor language covered in model training.
  - Routing axes: task `discovery`, comparison `none`, output `ranked_list`, evidence `vector`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: The embedding model represents the candidate phrases; candidate generation stays sane.
  - Null check: None; an output sample is reviewed against the documents.
  - Watch for: Model favoritism can rank trained-on language; scores are not importance.
  - Limits: Phrase previews only; not validated themes or importance ranking.
  - Workload: One encoding pass for documents and phrases; caching cuts repeat work.
  - Ask it: "Which phrases best describe what each document says?" / "Suggest key phrases for a summary preview of documents." / "Find candidate phrases similar in meaning to the document."
  - Follow-ups: "Do these phrases match what the document argues?" / "How does a seed term change the ranking?" / "Which documents share the same key phrases?"
  - Evidence: https://maartengr.github.io/KeyBERT/guides/embeddings.html (Documentation)
- **07 — Statistical keyphrase extraction (YAKE)** (op: `keyphrases_extract`). Get a quick lexical overview and compare it with embedding-based extraction.
  - Validate: Review language and filtering settings. Inspect short and long texts separately.
  - Claim only: Statistically salient phrases for the supplied texts.
  - Do not claim: Do not claim research importance for high-scoring phrases.
  - Cautions: Short texts and filtering choices can change the results. Bias: Surface salience can miss implicit or uncommon concepts.
  - Routing axes: task `discovery`, comparison `none`, output `ranked_list`, evidence `lexical`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: Text features alone carry salience; language settings match the corpus.
  - Null check: None; short and long texts are inspected separately.
  - Watch for: Short texts and filtering choices can change the ranked phrases.
  - Limits: Statistically salient phrases only; not research importance.
  - Workload: Local statistics over n units; no transformer is required.
  - Ask it: "Give me a quick phrase list without any model." / "Extract salient phrases using text statistics alone." / "What are the top phrases by statistical salience?"
  - Follow-ups: "How does this list compare with embedding-based phrases?" / "Which phrases survive stricter filtering?" / "Do short texts rank differently here?"
  - Evidence: https://doi.org/10.1016/j.ins.2019.09.013 (Journal 2019)
- **08 — Classical topic modeling (NMF or LDA)**. Navigate a corpus and form questions for close reading.
  - Validate: Compare preprocessing and topic counts. Inspect representative and poorly fitting records.
  - Claim only: Exploratory topic descriptions for navigation.
  - Do not claim: Do not read a topic weight as proof of a qualitative theme.
  - Cautions: Topic results change with preprocessing, topic count, and fitting choices. Bias: Frequent vocabulary and analyst-selected topic counts shape the results.
  - Routing axes: task `discovery`, comparison `none`, output `ranked_list`, evidence `lexical`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Topic count and preprocessing fit the corpus; wording stays stable.
  - Null check: None; fits across settings are compared by hand.
  - Watch for: Frequent vocabulary and analyst-chosen topic counts shape every result.
  - Limits: Exploratory navigation only; never proof of a qualitative theme.
  - Workload: Repeated sparse fitting over n documents; CPU only.
  - Ask it: "What topics run through this whole corpus?" / "Show topic word lists to explore the collection." / "I want to navigate themes across many documents."
  - Follow-ups: "How many topics fit this corpus best?" / "Which documents mix topics strongly?" / "What wording sits outside every topic?"
  - Evidence: https://www.jmlr.org/papers/v3/blei03a.html (Journal 2003)
- **09 — Unsupervised BERTopic** (op: `topics_build`). Build an exploratory corpus map and sample records for inspection.
  - Validate: Review segmentation and clustering settings. Inspect clusters and outliers. Repeat with controlled changes.
  - Claim only: Exploratory corpus maps with cluster descriptions.
  - Do not claim: Do not present a topic representation as a validated research theme.
  - Cautions: Clusters depend on embeddings, dimensionality reduction, and clustering settings. Bias: Small or dispersed groups can be treated as outliers.
  - Routing axes: task `discovery`, comparison `none`, output `cluster_map`, evidence `vector`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Embeddings group related text; reduction and clustering settings stay reviewed.
  - Null check: None; clusters and outlier records are inspected directly.
  - Watch for: Small or dispersed groups can be discarded as outliers.
  - Limits: Exploratory maps only; not validated research themes.
  - Workload: One embedding pass plus CPU grouping; repeated model searches add cost.
  - Ask it: "Discover topics from embeddings and describe each group." / "What clusters of discussion appear in these records?" / "Build an exploratory topic map of the corpus."
  - Follow-ups: "Which clusters look like real topics?" / "What did the model treat as outliers?" / "How stable are topics across settings?"
  - Evidence: https://arxiv.org/abs/2203.05794 (Preprint 2022)
- **10 — Guided BERTopic** (op: `topics_build`). Explore expected subject areas while checking material outside them.
  - Validate: Review seed coverage and alternatives. Compare against an unguided run.
  - Claim only: Topics shaped by declared seed concepts.
  - Do not claim: Do not claim the seeds produced complete or unbiased discovery.
  - Cautions: Seeds do not guarantee the wanted topics or complete discovery. Bias: Supplied topic vocabulary can create confirmation bias.
  - Routing axes: task `discovery`, comparison `none`, output `cluster_map`, evidence `vector`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Seed words point at real concepts; an unguided run stays comparable.
  - Null check: None; guided output is compared against an unguided run.
  - Watch for: Seed vocabulary can create confirmation bias and incomplete discovery.
  - Limits: Seed-shaped exploration only; not complete or unbiased discovery.
  - Workload: BERTopic workload plus seed comparisons over n text units.
  - Ask it: "Steer topic discovery toward my known subject areas." / "Can seed words focus the topics on my interests?" / "I want topics shaped by the concepts I supply."
  - Follow-ups: "How does the guided run differ from the unguided one?" / "Which seeds failed to shape a topic?" / "What topics formed outside my seeds?"
  - Evidence: https://maartengr.github.io/BERTopic/getting_started/guided/guided.html (Documentation)
- **11 — Zero-shot BERTopic** (op: `topics_build`). Combine known-topic routing with an explicit route for unexpected material.
  - Validate: Test descriptions and thresholds. Inspect assigned and unassigned text.
  - Claim only: Assignments to supplied topics, with the threshold recorded.
  - Do not claim: Do not equate this similarity matching with NLI classification.
  - Cautions: Description wording and thresholds affect assignment versus discovery. Bias: Familiar topics can absorb material that merits a different reading.
  - Routing axes: task `measurement`, comparison `none`, output `cluster_map`, evidence `vector`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Supplied descriptions match record wording; the similarity threshold stays tested.
  - Null check: None; assigned and unassigned records are inspected directly.
  - Watch for: Familiar topics can absorb material that merits a different reading.
  - Limits: Similarity routing only; not NLI classification.
  - Workload: One embedding pass, topic similarities, then grouping of the rest.
  - Ask it: "Route records onto my fixed list of topic descriptions." / "Match records to supplied topics and discover the rest." / "I have known topic names; sort records into them."
  - Follow-ups: "Which records landed on no supplied topic?" / "What new topics formed in the rest?" / "How sensitive is routing to the threshold?"
  - Evidence: https://maartengr.github.io/BERTopic/getting_started/zeroshot/zeroshot.html (Documentation)
- **12 — Embedding clustering (including interactive clustering)** (op: `clusters_inspect`). Organize review and compare alternative group structures.
  - Validate: Compare representations and grouping settings. Review boundary cases. Use feedback if available.
  - Claim only: Exploratory groups with examples and distances.
  - Do not claim: Do not claim semantic similarity equals the similarity the research question needs.
  - Cautions: A stable-looking group can reflect topic, style, speaker, or document format. Bias: Majority patterns and embedding training data shape the group structure.
  - Routing axes: task `discovery`, comparison `none`, output `cluster_map`, evidence `vector`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: Embedding similarity reflects the similarity the question needs; settings stay reviewed.
  - Null check: Compare mean silhouette against a permutation null before trusting the group count.
  - Watch for: Convexity bias flattens non-round clusters; imbalance hides minority groups.
  - Limits: Exploratory groups only; semantic similarity is not research similarity.
  - Workload: One embedding pass plus grouping; algorithm and corpus size matter.
  - Ask it: "Group similar comments together so I can review them." / "Cluster the embeddings and show example records per group." / "How can I organize thousands of records into groups?"
  - Follow-ups: "Which records sit between two groups?" / "How do groups change with different settings?" / "What makes each group's examples similar?"
  - Evidence: https://aclanthology.org/2026.nlp4dh-1.36.pdf (Workshop 2026)
- **13 — Embedding-centroid classification** (op: `centroids_classify`). Apply a small set of concrete categories with inspectable prototypes.
  - Validate: Inspect the examples. Measure category precision, recall, and abstention. Tune thresholds on separate data.
  - Claim only: Category assignments with similarities and recorded abstentions.
  - Do not claim: Do not claim one centroid represents every form of a category.
  - Cautions: One mean can hide distinct subgroups inside a category. Bias: Example selection defines the category prototype.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `vector`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: One mean per category represents it; examples cover the category spread.
  - Null check: Tune thresholds on separate data; report precision, recall, and abstention.
  - Watch for: One centroid can hide distinct subgroups inside a category.
  - Limits: A few concrete categories only; broad or many categories degrade fast.
  - Workload: Encode n records once; compare against a small prototype set.
  - Ask it: "Classify records into a few categories from example averages." / "I have labeled examples; assign new records by similarity." / "Which category is each record closest to overall?"
  - Follow-ups: "Which categories attract the most abstentions?" / "Do any categories hide two subgroups?" / "How do thresholds move precision and recall?"
  - Evidence: https://ar5iv.labs.arxiv.org/html/2211.16285 (Preprint 2022)
- **14 — Nearest-neighbor classification** (op: `neighbors_classify`). Support example-based review and find missing training examples.
  - Validate: Review example coverage. Tune the neighbor count and rejection rules.
  - Claim only: Category assignments backed by listed neighbor examples.
  - Do not claim: Do not claim coverage the labeled examples do not have.
  - Cautions: Sensitive to neighbor coverage, distance choice, and local label errors. Bias: Overrepresented examples can dominate nearby votes.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `vector`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: Labeled neighbors cover local regions; the distance choice fits the space.
  - Null check: Tune the neighbor count and rejection rules on held-out labels.
  - Watch for: Overrepresented examples dominate nearby votes; local label errors propagate.
  - Limits: Example-backed labels only; coverage stops where labels stop.
  - Workload: Encode n records once; neighbor search scales with index and corpus size.
  - Ask it: "Label this record from its nearest labeled neighbors." / "Which known examples support each predicted category?" / "I want predictions that show their supporting examples."
  - Follow-ups: "Which predictions rest on weak neighbor support?" / "Where are labeled examples sparse?" / "Which records get the most disagreement among neighbors?"
  - Evidence: https://aclanthology.org/D19-1410/ (Conference 2019)
- **15 — TF-IDF with logistic regression or linear SVM** (op: `lexical_classify`). Set a reproducible classification baseline before a more expensive model.
  - Validate: Fit the baseline. Inspect errors. Tune features without the final test set.
  - Claim only: Baseline performance on independent test records.
  - Do not claim: Do not claim transfer to shifted vocabulary without a test.
  - Cautions: Vocabulary shift and shortcut words can reduce transfer. Bias: Labels can attach to names, groups, or formatting instead of the concept.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `lexical`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: Training vocabulary matches test vocabulary; labels attach to the concept itself.
  - Null check: Measure baseline performance on independent test records.
  - Watch for: Vocabulary shift and shortcut words cut transfer; labels can attach to names.
  - Limits: Narrow categories on similar text; shifted vocabulary needs a new test.
  - Workload: Sparse feature extraction plus a linear fit over n records; low infrastructure burden.
  - Ask it: "Train a cheap text classifier for my narrow categories." / "Set a baseline classifier on word features first." / "Which words drive this linear text classifier?"
  - Follow-ups: "Which words dominate each coefficient?" / "Which labels confuse the classifier most?" / "Does the baseline hold on shifted vocabulary?"
  - Evidence: https://scikit-learn.org/stable/auto_examples/text/plot_document_classification_20newsgroups.html (Documentation)
- **16 — Frozen embeddings with a linear classifier** (op: `classification_build`). Run bounded classification when the frozen representation preserves the categories.
  - Validate: Measure category-level performance on an evaluation set. Audit errors.
  - Claim only: Category-level results tied to the recorded embedding-model version.
  - Do not claim: Do not claim the encoder preserves distinctions it was never shown.
  - Cautions: A fixed encoder cannot learn distinctions it does not represent. Bias: Training examples and pretrained representations both shape the decisions.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `vector`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: The frozen encoder represents the needed distinctions; the evaluation set stays honest.
  - Null check: Measure category-level performance on a labeled evaluation set.
  - Watch for: A fixed encoder cannot learn distinctions it does not represent.
  - Limits: Bounded categories the representation already separates.
  - Workload: One embedding pass plus a small classifier fit; embeddings are reused.
  - Ask it: "Train a small classifier on existing frozen embeddings." / "Classify with a linear head over fixed embeddings." / "I want to reuse cached embeddings for classification."
  - Follow-ups: "Which categories does the encoder separate least?" / "Where do classifier errors cluster?" / "Should the encoder stay frozen for new categories?"
  - Evidence: https://huggingface.co/papers/2209.11055 (Preprint 2022)
- **17 — SetFit few-shot classification** (op: `classification_build`). Improve a defined classification task when labeled examples are available.
  - Validate: Compare with frozen embeddings and a lexical baseline. Review errors.
  - Claim only: Few-shot results for the tested categories and example set.
  - Do not claim: Do not promise a published benchmark number for a new corpus.
  - Cautions: Few examples may not cover category diversity. Noisy examples harm adaptation. Bias: Small training sets can amplify analyst-selected examples.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `labels`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: The few examples cover category diversity; example quality stays high.
  - Null check: Compare against frozen embeddings and a lexical baseline on labeled data.
  - Watch for: Noisy or analyst-chosen few examples can amplify bias.
  - Limits: The tested categories and example set only; never a promised benchmark number.
  - Workload: Encoder training plus inference over n records; more setup compute than frozen.
  - Ask it: "Improve classification with only a few labeled examples." / "Adapt the sentence encoder using labeled text pairs." / "Few shot learning for categories the baseline misses."
  - Follow-ups: "How few examples still move accuracy?" / "Which pairs improved the encoder most?" / "Does the adapted encoder transfer to sibling categories?"
  - Evidence: https://huggingface.co/papers/2209.11055 (Preprint 2022)
- **18 — Zero-shot NLI classification** (op: `nli_classify`). Triage records or propose labels before any labeled data exists.
  - Validate: Test paraphrased descriptions, negation, and overlapping labels. Tune thresholds on labeled data.
  - Claim only: Pilot label proposals with support scores.
  - Do not claim: Do not treat zero-shot scores as established probabilities.
  - Cautions: Wording, lexical overlap, and syntax can produce wrong support judgments. Bias: Label descriptions and training-domain assumptions shape the results.
  - Routing axes: task `measurement`, comparison `none`, output `point_answer`, evidence `claims`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: Label statements are clear and distinct; wording avoids lexical-overlap traps.
  - Null check: Tune thresholds on labeled data; test paraphrases and negation.
  - Watch for: Lexical overlap and syntax can flip support judgments.
  - Limits: Pilot triage only; scores are not established probabilities.
  - Workload: Pair inference grows with record count and label count.
  - Ask it: "Test candidate labels before any labeled data exists." / "Does this text support the statement my label makes?" / "Score records against written label descriptions."
  - Follow-ups: "Which label statements overlap too much?" / "Where does negation flip the verdict?" / "Which categories need labeled examples next?"
  - Evidence: https://aclanthology.org/D19-1404/ (Conference 2019)
- **19 — Programmatic weak supervision (Snorkel)** (op: `labeling_build`). Turn reusable domain rules into training data. Keep an independent human evaluation set.
  - Validate: Inspect the labeling functions. Measure conflicts and coverage. Evaluate on independent labels.
  - Claim only: Training labels with coverage and conflict reports.
  - Do not claim: Do not claim label quality without an independent evaluation set.
  - Cautions: Correlated rules can repeat the same error. Low coverage stays a problem. Bias: Rule authors can encode the same assumptions in several functions.
  - Routing axes: task `prediction`, comparison `none`, output `point_answer`, evidence `labels`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: Labeling functions fire independently; coverage stays high enough to matter.
  - Null check: Evaluate on independent human labels; report coverage and conflicts.
  - Watch for: Correlated rules repeat one error; low coverage stays a problem.
  - Limits: Training labels only; label quality needs an independent evaluation set.
  - Workload: Rule execution plus label-model fitting over n records; a classifier adds cost.
  - Ask it: "Turn my labeling rules into training labels at scale." / "Combine several imperfect rules into one label set." / "I have many weak rules; merge their votes."
  - Follow-ups: "Which rules conflict most often?" / "How much coverage do the rules miss?" / "Where do model labels disagree with humans?"
  - Evidence: https://link.springer.com/content/pdf/10.1007/s00778-019-00552-1.pdf (Journal 2019)
- **20 — Active learning loop** (op: `active_learn_next`). Reduce annotation effort for a defined task. Keep the final evaluation sample separate.
  - Validate: Assess improvement per annotation effort. Compare against random sampling.
  - Claim only: Annotation-order recommendations with the strategy recorded.
  - Do not claim: Do not claim the selection strategy always beats random sampling.
  - Cautions: Uncertainty sampling can chase difficult or noisy cases, not representative ones. Bias: Selected annotation data need not match corpus prevalence.
  - Routing axes: task `prediction`, comparison `none`, output `ranked_list`, evidence `labels`, rigor `inferential_calibrated`, cost tier `batch`.
  - Assumes: The task and label set stay stable; annotators stay consistent.
  - Null check: Compare each round against random sampling on the same budget.
  - Watch for: Uncertainty sampling chases noisy cases, not representative ones.
  - Limits: Annotation ordering only; the strategy need not beat random sampling.
  - Workload: Repeated scoring and retraining; human annotation is the limiting resource.
  - Ask it: "Which records should I label next to save effort?" / "Pick the most useful examples for my annotation round." / "I want the classifier to choose what I label."
  - Follow-ups: "How much did accuracy move per batch?" / "Does this strategy beat random sampling here?" / "Which records stay uncertain after retraining?"
  - Evidence: https://burrsettles.com/pub/settles.activelearning.pdf (Survey 2009)
- **21 — Human-in-the-loop computational grounded theory**. Develop and challenge an explanation with the corpus.
  - Validate: Test the explanation against close reading, alternative readings, and further evidence.
  - Claim only: An interpreted account that survived stated tests.
  - Do not claim: Do not present it as autonomous theme discovery.
  - Cautions: Computational patterns do not remove the need for contextual interpretation. Bias: Pattern selection and theoretical interpretation can reinforce prior beliefs.
  - Routing axes: task `discovery`, comparison `none`, output `narrative`, evidence `claims`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Interpretation stays open to challenge; computational patterns stay evidence, not proof.
  - Null check: None; the account is tested against close reading and alternative readings.
  - Watch for: Pattern selection can reinforce prior beliefs instead of testing them.
  - Limits: Manual close reading wins for interpretive claims; computation only assists.
  - Workload: Several human analysis cycles; no simple runtime estimate exists.
  - Ask it: "Develop and test an explanation for these patterns." / "Combine computational pattern finding with human interpretation." / "I want theory driven analysis with computational help."
  - Follow-ups: "What evidence challenges my current explanation?" / "Which patterns survived the close reading test?" / "Should the next cycle test a rival reading?"
  - Evidence: https://doi.org/10.1177/0049124117729705 (Journal 2020)
- **22 — Extract, embed, cluster, summarize (EECS)** (op: `analyze_run`). Produce a structured discovery aid. Audit extraction omissions before reading the groups.
  - Validate: Evaluate extraction coverage, grouping quality, and summary fidelity separately.
  - Claim only: A structured discovery aid with per-stage audit results.
  - Do not claim: Do not claim end-to-end accuracy from the components' evidence.
  - Cautions: Early extraction omissions cannot be repaired by later clustering or summaries. Bias: Extraction rules, grouping, and summary selection can each drop minority evidence.
  - Routing axes: task `discovery`, comparison `none`, output `narrative`, evidence `vector`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Extraction covers the evidence; grouping and summaries preserve it.
  - Null check: Evaluate extraction coverage, grouping quality, and summary fidelity separately.
  - Watch for: Early extraction omissions cannot be repaired by later clustering or summaries.
  - Limits: A discovery aid only; not end-to-end accuracy claims.
  - Workload: Extraction, encoding, grouping, and summarization over n spans; cache each stage.
  - Ask it: "Extract passages, group them, and summarize each group." / "Give me structured summaries built from source evidence." / "I want grouped evidence with short human readable summaries."
  - Follow-ups: "What did extraction drop that groups cannot recover?" / "Which summaries need the original passages?" / "How faithful is each summary to its group?"
  - Evidence: https://aclanthology.org/W04-3252/ (Workshop 2004)
- **23 — Hybrid retrieval with reranking** (op: `search`). Assemble evidence for a question and inspect counter-evidence.
  - Validate: Check known relevant passages, missed evidence, and question variants.
  - Claim only: Evidence lists for stated questions, with scores by type.
  - Do not claim: Do not read retrieval scores as answer truth.
  - Cautions: Performance can change across retrieval tasks and domains. Bias: Questions and ranking models decide which evidence becomes visible.
  - Routing axes: task `discovery`, comparison `none`, output `ranked_list`, evidence `vector`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: Queries match the evidence wording; the index stays current.
  - Null check: Check known relevant passages and missed evidence across question variants.
  - Watch for: Question wording and reranker choice decide which evidence becomes visible.
  - Limits: Evidence lists only; retrieval scores are not answer truth.
  - Workload: Index once over n records; rerank only a bounded candidate set.
  - Ask it: "Find passages that answer my question across the corpus." / "Retrieve the best evidence for this question." / "Search by meaning and keywords together, then rerank."
  - Follow-ups: "Which relevant passages did retrieval miss?" / "How do scores differ across retriever types?" / "What wording of the question finds counter evidence?"
  - Evidence: https://arxiv.org/abs/2104.08663 (Preprint 2021)
- **24 — Extractive question answering** (op: `ask`). Fill narrow evidence fields where the answer is stated in the source.
  - Validate: Test representative questions. Tune no-answer handling. Inspect unsupported spans.
  - Claim only: Stated-answer spans with source locations.
  - Do not claim: Do not claim answers to questions the passages do not answer.
  - Cautions: A plausible span can be wrong or unsupported. No-answer behavior matters. Bias: Question wording and passage selection constrain the possible answers.
  - Routing axes: task `measurement`, comparison `none`, output `point_answer`, evidence `claims`, rigor `descriptive`, cost tier `interactive`.
  - Assumes: Some passage states the answer; no-answer handling stays tuned.
  - Null check: Test representative questions; inspect unsupported spans by hand.
  - Watch for: A plausible span can be wrong or unsupported.
  - Limits: Stated answers only; open questions need close reading.
  - Workload: Question-passage pair inference; cost grows with candidate passages.
  - Ask it: "Which passage states the exact answer to my question?" / "Pull the answer span from the supplied passages." / "I want the source location for each stated answer."
  - Follow-ups: "Which answers rest on one passage only?" / "Where did no-answer handling refuse correctly?" / "Do answer spans hold under rephrased questions?"
  - Evidence: https://aclanthology.org/D16-1264/ (Conference 2016)
- **25 — Named entity recognition (including GLiNER)** (op: `ingest_text`). Create inventories and filters, or prepare data for entity resolution.
  - Validate: Define types and boundary rules. Label a span-level test set. Tune the model or thresholds.
  - Claim only: Span inventories for the declared entity types.
  - Do not claim: Do not assume two matching names are the same entity.
  - Cautions: Entity boundaries, unfamiliar terms, and domain shift can reduce performance. Bias: Type definitions and training data can favor familiar names or entity forms.
  - Routing axes: task `representation`, comparison `none`, output `graph`, evidence `graph`, rigor `descriptive`, cost tier `batch`.
  - Assumes: Type descriptions match the corpus; boundaries follow declared rules.
  - Null check: Label a span-level test set; tune thresholds against it.
  - Watch for: Entity boundaries, unfamiliar terms, and domain shift cut performance.
  - Limits: Span inventories only; matching names are not the same entity.
  - Workload: Token or span inference over n records; batch short passages.
  - Ask it: "Find product names, organizations, and places in the text." / "Extract named entities with their types and offsets." / "I want an inventory of entities mentioned in records."
  - Follow-ups: "Which entity types confuse the extractor?" / "How do boundaries fail on unfamiliar names?" / "Which mentions never matched a declared type?"
  - Evidence: https://aclanthology.org/2024.emnlp-main.660/ (Conference 2024)
- **26 — Sentiment or emotion classification** (op: `sentiment_screen`). Review explicit evaluative language under a defined construct.
  - Validate: Define the construct. Label and test each category, including neutral or uncertain cases.
  - Claim only: Screening flags for evaluative language under the defined construct.
  - Do not claim: Do not infer a participant's internal state without further evidence.
  - Cautions: Mixed feelings, indirect language, and code-mixed language need task-specific tests. Bias: Expression style can be mistaken for an emotional state.
  - Routing axes: task `measurement`, comparison `none`, output `statistic`, evidence `labels`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: The construct is defined; expression maps to the stated categories.
  - Null check: Label and test each category, including neutral and mixed cases.
  - Watch for: Indirect, mixed, and code-mixed language need task-specific tests.
  - Limits: Screening flags only; not a participant's internal state.
  - Workload: A rule scan, one classifier pass, or NLI pairs over n records.
  - Ask it: "How positive or negative is the tone of these records?" / "Screen records for evaluative or emotional language." / "Which records express frustration or satisfaction?"
  - Follow-ups: "Which records look mixed or neutral?" / "Does tone match the words screened?" / "Which categories need their own test set?"
  - Evidence: https://aclanthology.org/2023.wassa-1.59/ (Workshop 2023)
- **27 — Aspect-based sentiment analysis** (op: `sentiment_screen`). Compare evaluations of defined product or service attributes.
  - Validate: Evaluate aspect extraction and target-linked sentiment separately, then jointly.
  - Claim only: Aspect-linked sentiment for the declared aspects.
  - Do not claim: Do not claim opinions on aspects the definitions never covered.
  - Cautions: Correct sentiment requires the correct target and aspect link. Bias: Predefined aspects can exclude concerns outside the team's framework.
  - Routing axes: task `measurement`, comparison `subset_vs_subset`, output `point_answer`, evidence `labels`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: Aspects are declared; target links in the text are explicit enough.
  - Null check: Evaluate aspect extraction and target-linked sentiment separately, then jointly.
  - Watch for: Wrong target links flip sentiment; declared aspects exclude unlisted concerns.
  - Limits: Declared aspects only; uncovered aspects stay unmeasured.
  - Workload: Extraction plus sentiment assignment over n records; stages may combine.
  - Ask it: "What do reviewers say about the battery life specifically?" / "Compare opinions about each product feature separately." / "I want sentiment linked to the aspect it targets."
  - Follow-ups: "Which aspects get conflicting sentiment?" / "What opinions fall outside declared aspects?" / "How do targets link across sentences?"
  - Evidence: https://aclanthology.org/S14-2004/ (Workshop 2014)
- **28 — NLI-based contradiction screening** (op: `contradictions_screen`). Build a contradiction-review queue for human reviewers.
  - Validate: Test negation, entity identity, time, conditions, and statements that merely differ.
  - Claim only: Candidate contradictions for human adjudication.
  - Do not claim: Do not claim a model decision proves a claim false in the world.
  - Cautions: Different times or conditions can make apparent contradictions compatible. Bias: Candidate selection can favor disagreement and miss agreement or context.
  - Routing axes: task `measurement`, comparison `temporal`, output `ranked_list`, evidence `claims`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: Compared statements share time and conditions; entity identity is settled.
  - Null check: Test negation, time, and conditions on labeled statement pairs.
  - Watch for: Different times or conditions make apparent contradictions compatible.
  - Limits: A candidate queue for humans; model decisions never prove a claim false.
  - Workload: Retrieval plus pair scoring over k candidates; never all pairs.
  - Ask it: "Find statements that contradict each other across records." / "Which claims conflict when compared pair by pair?" / "I want a review queue of possible contradictions."
  - Follow-ups: "Which pairs only differ by time or place?" / "Do flagged pairs survive reading the context?" / "Which speakers change position across records?"
  - Evidence: https://aclanthology.org/P19-1334/ (Conference 2019)
- **29 — Near-duplicate and paraphrase detection** (op: `duplicates_screen`). Remove duplicate ingestion and find repeated material. Preserve independent participant records.
  - Validate: Measure pairwise precision and recall at the action threshold. Label candidate pairs.
  - Claim only: Duplicate candidates at a recorded threshold, for human approval.
  - Do not claim: Do not merge records without human approval of the merge policy.
  - Cautions: Similar meaning does not establish interchangeable evidence. Bias: Merging can erase independent speakers or minority wording.
  - Routing axes: task `measurement`, comparison `none`, output `ranked_list`, evidence `vector`, rigor `inferential_calibrated`, cost tier `interactive`.
  - Assumes: Similar meaning at the threshold implies interchangeable evidence; thresholds stay separate.
  - Null check: Measure pairwise precision and recall at the action threshold.
  - Watch for: Merging can erase independent speakers or minority wording.
  - Limits: Candidate pairs for human approval; never auto-merge.
  - Workload: Hashing over n records plus neighbor search for semantic candidates.
  - Ask it: "Find duplicate or paraphrased records before analysis." / "Which records say nearly the same thing?" / "I want to merge near duplicates without losing speakers."
  - Follow-ups: "Which near duplicates are genuinely separate speakers?" / "How do thresholds split review from merge?" / "What paraphrases survived the exact-match pass?"
  - Evidence: https://aclanthology.org/D19-1410/ (Conference 2019)
- **30 — Outlier or novelty detection** (op: `outliers_rank`). Find exceptions, data problems, and possibly overlooked experiences.
  - Validate: Define the reference group. Inspect flagged records. Compare detectors and representations.
  - Claim only: A ranked review list with the detector and reference recorded.
  - Do not claim: Do not equate unusualness with importance.
  - Cautions: Results depend on the representation and the definition of normality. Bias: Minority language, rare experiences, or accessibility needs can look anomalous.
  - Routing axes: task `measurement`, comparison `subset_vs_subset`, output `ranked_list`, evidence `vector`, rigor `inferential_null`, cost tier `interactive`.
  - Assumes: Normality is defined by the reference group; the representation fits it.
  - Null check: Compare detector scores against the reference distribution before ranking.
  - Watch for: Minority language or rare experiences can look anomalous.
  - Limits: A ranked review list only; unusualness is not importance.
  - Workload: Encode n records once; some neighbor detectors scale poorly.
  - Ask it: "Which records are unusual compared with the rest?" / "Rank the strangest records for my review." / "I want to find exceptions and possible data problems."
  - Follow-ups: "Which flagged records are rare but normal?" / "How does the detector change with the reference group?" / "What does the nearest ordinary example show?"
  - Evidence: https://scikit-learn.org/stable/modules/outlier_detection.html (Documentation)
<!-- amr-register:generated:end -->
