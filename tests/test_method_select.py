"""The analysis_select_method handler: vocab gates, passthrough, golden.

The golden snapshot freezes ONE full envelope as JSON (sort_keys) and
byte-compares it. Same query, same bytes, always — the determinism
contract for the selector. Regenerate the constant by running the
golden query and serializing with json.dumps(result, sort_keys=True).
"""

from __future__ import annotations

import json
from typing import cast

import asyncpg
import pytest

from corpus_kb.analysis.method_register import load_method_register
from corpus_kb.analysis.method_register_rubric import rank_methods
from corpus_kb.errors import ValidationError
from corpus_kb.tools.method_select_tools import analysis_select_method

TASK = "Rank methods for screening near-duplicate interview content."
GOLDEN_FAMILY = "errors"
GOLDEN_INPUTS = ["embeddings", "raw_text"]
GOLDEN_TIER = "baseline"

# The frozen full envelope for the golden query against register
# version 1.0.0 (one line, sort_keys; the determinism pin).
GOLDEN_JSON = """{"recommendations": [{"autonomy": "automated_with_audit", "bias_risk": "Merging can erase independent speakers or minority wording.", "calibration_effort": "moderate", "complexity_tier": "baseline", "do_not_claim": "Do not merge records without human approval of the merge policy.", "efficiency_workload": "Hashing over n records for exact duplicates. Embeddings and neighbor search for semantic candidates.", "evidence_label": "model_based", "id": "29", "implementation_status": "implemented_new", "inputs_required": ["raw_text", "embeddings"], "missing_inputs": [], "name": "Near-duplicate and paraphrase detection", "op": "duplicates_screen", "output_family": "errors", "output_shape": "Candidate pairs or groups, similarity values, and merge decisions.", "paradigm": "unsupervised", "permitted_claims": "Duplicate candidates at a recorded threshold, for human approval.", "rank": 1, "rank_reason": "Output family matches the request.", "robustness_caveat": "Similar meaning does not establish interchangeable evidence.", "selection_notes": "Next for duplicate control before analysis. Keep review and merge thresholds separate.", "selection_priority": "next", "sources": ["https://aclanthology.org/D19-1410/", "https://www.sbert.net/examples/applications/paraphrase-mining/README.html"], "summary": "Find records with the same or similar wording. Use exact matching and semantic comparison together.", "usage_guidance": "Remove duplicate ingestion and find repeated material. Preserve independent participant records.", "validation_required": "Measure pairwise precision and recall at the action threshold. Label candidate pairs."}, {"autonomy": "human_in_loop", "bias_risk": "Minority language, rare experiences, or accessibility needs can look anomalous.", "calibration_effort": "moderate", "complexity_tier": "baseline", "do_not_claim": "Do not equate unusualness with importance.", "efficiency_workload": "Encode n records once. Detector fitting and scoring. Some neighbor methods scale poorly.", "evidence_label": "model_based", "id": "30", "implementation_status": "implemented_new", "inputs_required": ["raw_text", "embeddings"], "missing_inputs": [], "name": "Outlier or novelty detection", "op": "outliers_rank", "output_family": "errors", "output_shape": "Ranked unusual records, detector scores, and nearest ordinary examples.", "paradigm": "unsupervised", "permitted_claims": "A ranked review list with the detector and reference recorded.", "rank": 2, "rank_reason": "All axes are equal. The id order decides.", "robustness_caveat": "Results depend on the representation and the definition of normality.", "selection_notes": "Next for finding exceptions and data problems after a baseline screen.", "selection_priority": "next", "sources": ["https://scikit-learn.org/stable/modules/outlier_detection.html", "https://aclanthology.org/2026.stil-1.20.pdf"], "summary": "Rank records that differ from a reference distribution or from their neighbors.", "usage_guidance": "Find exceptions, data problems, and possibly overlooked experiences.", "validation_required": "Define the reference group. Inspect flagged records. Compare detectors and representations."}, {"autonomy": "automated_with_audit", "bias_risk": "Vocabulary choices can favor some groups or ways of speaking.", "calibration_effort": "low", "complexity_tier": "baseline", "do_not_claim": "Do not claim rule matches prove implicit meaning.", "efficiency_workload": "Text scanning only. No neural inference is required.", "evidence_label": "direct", "id": "04", "implementation_status": "implemented_existing", "inputs_required": ["raw_text"], "missing_inputs": [], "name": "Dictionary or rule-based classification", "op": "labeling_build", "output_family": "labels", "output_shape": "Matched spans, rule ids, category assignments, exclusions, and unmatched records.", "paradigm": "supervised", "permitted_claims": "Counts of rule matches, unless broader validity is established.", "rank": 3, "rank_reason": "Output family does not match the request.", "robustness_caveat": "Matches are sensitive to synonyms, negation, spelling, and context.", "selection_notes": "First for explicit mentions of a known issue. Check the non-matches too.", "selection_priority": "first", "sources": ["https://link.springer.com/content/pdf/10.1007/s00778-019-00552-1.pdf", "https://docs.v1.argilla.io/en/v1.10.0/reference/webapp/features.html"], "summary": "Match defined words, phrases, or patterns to categories. Rules can assign a category or abstain.", "usage_guidance": "Find explicit mentions and build a review queue.", "validation_required": "Inspect matches and non-matches. Add exclusions. Test the rules on independent records."}, {"autonomy": "automated_with_audit", "bias_risk": "Rare wording can rank highly without research importance.", "calibration_effort": "low", "complexity_tier": "baseline", "do_not_claim": "Do not call the ranked list themes.", "efficiency_workload": "Sparse text-matrix construction and ranking over n text units. CPU-oriented.", "evidence_label": "direct", "id": "05", "implementation_status": "implemented_new", "inputs_required": ["raw_text"], "missing_inputs": [], "name": "TF-IDF keyphrase scan", "op": "keyphrases_extract", "output_family": "themes", "output_shape": "Ranked terms and phrases with TF-IDF weights and document links.", "paradigm": "unsupervised", "permitted_claims": "Descriptions of distinctive wording in the corpus.", "rank": 4, "rank_reason": "All axes are equal. The id order decides.", "robustness_caveat": "Sensitive to document boundaries, vocabulary filtering, and repeated boilerplate.", "selection_notes": "First to understand the main vocabulary. YAKE (07) is the low-setup peer.", "selection_priority": "first", "sources": ["https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfTransformer.html", "https://scikit-learn.org/1.5/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html"], "summary": "Rank words and word sequences by document frequency and corpus rarity to find distinctive wording.", "usage_guidance": "Inspect corpus vocabulary, find search terms, and improve retrieval.", "validation_required": "Review tokenization, common words, phrase lengths, and the top-ranked terms."}, {"autonomy": "automated_with_audit", "bias_risk": "Surface salience can miss implicit or uncommon concepts.", "calibration_effort": "low", "complexity_tier": "baseline", "do_not_claim": "Do not claim research importance for high-scoring phrases.", "efficiency_workload": "Local statistical text processing over n text units. No transformer is required.", "evidence_label": "direct", "id": "07", "implementation_status": "implemented_new", "inputs_required": ["raw_text"], "missing_inputs": [], "name": "Statistical keyphrase extraction (YAKE)", "op": "keyphrases_extract", "output_family": "themes", "output_shape": "Ranked phrases with algorithm scores.", "paradigm": "unsupervised", "permitted_claims": "Statistically salient phrases for the supplied texts.", "rank": 5, "rank_reason": "All axes are equal. The id order decides.", "robustness_caveat": "Short texts and filtering choices can change the results.", "selection_notes": "First for a low-setup lexical overview. Compare it with 06.", "selection_priority": "first", "sources": ["https://doi.org/10.1016/j.ins.2019.09.013", "https://aclanthology.org/2022.findings-acl.34.pdf"], "summary": "Extract phrases from text features alone. No fitted model or embedding step is needed.", "usage_guidance": "Get a quick lexical overview and compare it with embedding-based extraction.", "validation_required": "Review language and filtering settings. Inspect short and long texts separately."}, {"autonomy": "human_in_loop", "bias_risk": "Template and interview-guide framing can compress the summaries.", "calibration_effort": "moderate", "complexity_tier": "baseline", "do_not_claim": "Do not claim the matrix equals a full line-by-line analysis.", "efficiency_workload": "Compute is minor. Human summarization and comparison dominate the workload.", "evidence_label": "human_judgment", "id": "02", "implementation_status": "implemented_existing", "inputs_required": ["raw_text"], "missing_inputs": [], "name": "Rapid Qualitative Analysis (RQA) matrix", "op": "analyze_run", "output_family": "structures", "output_shape": "Rows for cases, columns for questions, short summaries, and source links.", "paradigm": "human_led", "permitted_claims": "Bounded answers for the templated questions, with links to evidence.", "rank": 6, "rank_reason": "Calibration effort is higher than the record above.", "robustness_caveat": "Summary templates can omit detail that later analysis needs.", "selection_notes": "First for question-by-case comparison of interview material.", "selection_priority": "first", "sources": ["https://pmc.ncbi.nlm.nih.gov/articles/PMC11877799/", "https://shura.shu.ac.uk/30222/9/Swallow-APragmaticGuideToQualitative(AM).pdf"], "summary": "Analysts place short summaries in a structured template, then compare cases in a matrix.", "usage_guidance": "Answer a bounded set of questions. Read the original evidence before consequential decisions.", "validation_required": "Pilot the template. Have another analyst inspect the summaries. Revise missing or ambiguous fields."}, {"autonomy": "automated_with_audit", "bias_risk": "Expression style can be mistaken for an emotional state.", "calibration_effort": "moderate", "complexity_tier": "baseline", "do_not_claim": "Do not infer a participant's internal state without further evidence.", "efficiency_workload": "Depends on the implementation: rule scan, one classifier pass, or NLI pairs over n records.", "evidence_label": "preset_composition", "id": "26", "implementation_status": "implemented_new", "inputs_required": ["raw_text"], "missing_inputs": [], "name": "Sentiment or emotion classification", "op": "sentiment_screen", "output_family": "scores", "output_shape": "Affect labels, scores, source text, and uncertain or mixed cases.", "paradigm": "weak_supervision", "permitted_claims": "Screening flags for evaluative language under the defined construct.", "rank": 7, "rank_reason": "Selection priority ranks it lower.", "robustness_caveat": "Mixed feelings, indirect language, and code-mixed language need task-specific tests.", "selection_notes": "Next for a bounded affect screen under a defined construct.", "selection_priority": "next", "sources": ["https://huggingface.co/docs/setfit/main/en/quickstart", "https://aclanthology.org/2023.wassa-1.59/"], "summary": "Assign defined affect categories to text. Rules, classifiers, or the methods above can implement it.", "usage_guidance": "Review explicit evaluative language under a defined construct.", "validation_required": "Define the construct. Label and test each category, including neutral or uncertain cases."}, {"autonomy": "automated_with_audit", "bias_risk": "Example selection defines the category prototype.", "calibration_effort": "low", "complexity_tier": "baseline", "do_not_claim": "Do not claim one centroid represents every form of a category.", "efficiency_workload": "Encode n records once. Compare against a small prototype set.", "evidence_label": "model_based", "id": "13", "implementation_status": "implemented_new", "inputs_required": ["embeddings", "labels"], "missing_inputs": ["labels"], "name": "Embedding-centroid classification", "op": "centroids_classify", "output_family": "labels", "output_shape": "Category similarities, the selected category, prototype examples, and abstention.", "paradigm": "supervised", "permitted_claims": "Category assignments with similarities and recorded abstentions.", "rank": 8, "rank_reason": "Required inputs are missing: labels.", "robustness_caveat": "One mean can hide distinct subgroups inside a category.", "selection_notes": "First for a few concrete categories when embeddings and labels exist.", "selection_priority": "first", "sources": ["https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.NearestCentroid.html", "https://ar5iv.labs.arxiv.org/html/2211.16285"], "summary": "Represent each category by the mean embedding of its examples, then assign records by similarity.", "usage_guidance": "Apply a small set of concrete categories with inspectable prototypes.", "validation_required": "Inspect the examples. Measure category precision, recall, and abstention. Tune thresholds on separate data."}, {"autonomy": "automated_with_audit", "bias_risk": "Overrepresented examples can dominate nearby votes.", "calibration_effort": "low", "complexity_tier": "baseline", "do_not_claim": "Do not claim coverage the labeled examples do not have.", "efficiency_workload": "Encode n records once. Neighbor search cost depends on the index and corpus size.", "evidence_label": "model_based", "id": "14", "implementation_status": "implemented_new", "inputs_required": ["embeddings", "labels"], "missing_inputs": ["labels"], "name": "Nearest-neighbor classification", "op": "neighbors_classify", "output_family": "labels", "output_shape": "Predicted categories with nearest labeled examples, distances, and votes.", "paradigm": "supervised", "permitted_claims": "Category assignments backed by listed neighbor examples.", "rank": 9, "rank_reason": "Selection priority ranks it lower.", "robustness_caveat": "Sensitive to neighbor coverage, distance choice, and local label errors.", "selection_notes": "Next when inspectable neighbor support matters more than a single prototype.", "selection_priority": "next", "sources": ["https://scikit-learn.org/stable/modules/neighbors.html", "https://aclanthology.org/D19-1410/"], "summary": "Predict a category from the labels of nearby examples. The individual examples are kept.", "usage_guidance": "Support example-based review and find missing training examples.", "validation_required": "Review example coverage. Tune the neighbor count and rejection rules."}, {"autonomy": "automated_with_audit", "bias_risk": "Labels can attach to names, groups, or formatting instead of the concept.", "calibration_effort": "moderate", "complexity_tier": "baseline", "do_not_claim": "Do not claim transfer to shifted vocabulary without a test.", "efficiency_workload": "Sparse feature extraction and linear fitting over n records. Low infrastructure burden.", "evidence_label": "model_based", "id": "15", "implementation_status": "implemented_new", "inputs_required": ["raw_text", "labels"], "missing_inputs": ["labels"], "name": "TF-IDF with logistic regression or linear SVM", "op": "lexical_classify", "output_family": "labels", "output_shape": "Labels, model scores, influential features, and error tables.", "paradigm": "supervised", "permitted_claims": "Baseline performance on independent test records.", "rank": 10, "rank_reason": "Calibration effort is higher than the record above.", "robustness_caveat": "Vocabulary shift and shortcut words can reduce transfer.", "selection_notes": "First for a cheap classification baseline on defined categories.", "selection_priority": "first", "sources": ["https://scikit-learn.org/stable/auto_examples/text/plot_document_classification_20newsgroups.html", "https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfTransformer.html"], "summary": "Train a logistic or linear SVM classifier on word or character features for narrow categories.", "usage_guidance": "Set a reproducible classification baseline before a more expensive model.", "validation_required": "Fit the baseline. Inspect errors. Tune features without the final test set."}], "register_version": "1.0.0", "status": "success"}"""  # noqa: E501


async def _run(
    task_description: str = TASK,
    output_family: str | None = None,
    inputs_available: list[str] | None = None,
    max_complexity_tier: str | None = None,
) -> dict[str, object]:
    return await analysis_select_method(
        cast("asyncpg.Pool", None),  # the selector reads no database
        task_description=task_description,
        output_family=output_family,
        inputs_available=inputs_available,
        max_complexity_tier=max_complexity_tier,
    )


async def test_empty_task_rejected() -> None:
    for task in ("", "   "):
        with pytest.raises(ValidationError) as excinfo:
            await _run(task_description=task)
        assert "task_description" in str(excinfo.value)
        assert excinfo.value.remediation


async def test_output_family_vocab_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await _run(output_family="vibes")
    message = str(excinfo.value)
    assert "output_family" in message
    assert "labels" in message and "themes" in message
    assert excinfo.value.remediation is not None


async def test_complexity_tier_vocab_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await _run(max_complexity_tier="heroic")
    message = str(excinfo.value)
    assert "max_complexity_tier" in message
    assert "baseline" in message and "standard" in message
    assert excinfo.value.remediation is not None


async def test_inputs_vocab_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        await _run(inputs_available=["embeddings", "vibes"])
    message = str(excinfo.value)
    assert "inputs_available" in message
    assert "vibes" in message
    assert "raw_text" in message
    assert excinfo.value.remediation is not None


async def test_ranking_passthrough_matches_the_one_rubric() -> None:
    """The handler wraps rank_methods; the rows must be the rubric's."""
    result = await _run(
        output_family=GOLDEN_FAMILY,
        inputs_available=GOLDEN_INPUTS,
        max_complexity_tier=GOLDEN_TIER,
    )
    register = load_method_register({})
    expected = rank_methods(register, GOLDEN_FAMILY, GOLDEN_INPUTS, GOLDEN_TIER)
    assert result["status"] == "success"
    assert result["register_version"] == register.register_version
    assert result["recommendations"] == [row.model_dump(mode="json") for row in expected]


async def test_golden_envelope_is_byte_stable() -> None:
    """Two runs, one frozen envelope: same query, same bytes."""
    first = json.dumps(
        await _run(
            output_family=GOLDEN_FAMILY,
            inputs_available=GOLDEN_INPUTS,
            max_complexity_tier=GOLDEN_TIER,
        ),
        sort_keys=True,
    )
    second = json.dumps(
        await _run(
            output_family=GOLDEN_FAMILY,
            inputs_available=GOLDEN_INPUTS,
            max_complexity_tier=GOLDEN_TIER,
        ),
        sort_keys=True,
    )
    assert first == second == GOLDEN_JSON


async def test_unconstrained_ranks_all_thirty() -> None:
    """No active constraint: every register row ranks once, in rubric order."""
    result = await _run()
    recommendations = cast("list[dict[str, object]]", result["recommendations"])
    assert len(recommendations) == 30
    ids = [str(row["id"]) for row in recommendations]
    assert len(set(ids)) == 30
