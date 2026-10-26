"""Wave 4 validator unit tests: pure functions over the canonical document.

Each validator (offset invariant, coverage, budget) has pass and fail
fixtures. Findings are data; the deterministic order is pinned.
"""

from __future__ import annotations

from corpus_kb.tools.pipeline.document import IngestDocument
from corpus_kb.tools.pipeline.validate import (
    budget_findings,
    coverage_findings,
    offset_invariant_findings,
    run_validators,
)
from corpus_kb.utils.models import Chunk, Document

DOC_ID = "doc-1"


def _doc(text: str, chunks: list[Chunk]) -> IngestDocument:
    return IngestDocument(
        document=Document(path="raw_text", source_type="text", content=text, size_bytes=len(text)),
        text=text,
        file_hash="0" * 64,
        source_type="text",
        path="raw_text",
        chunks=chunks,
    )


def _chunk(text: str, start: int | None, end: int | None, chunk_id: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=DOC_ID,
        text=text,
        source_type="text",
        source_start_char=start,
        source_end_char=end,
    )


class TestOffsetInvariant:
    def test_anchored_chunks_pass(self) -> None:
        text = "alpha beta gamma"
        chunks = [_chunk("alpha beta", 0, 10, "c1"), _chunk("gamma", 11, 16, "c2")]
        assert offset_invariant_findings(_doc(text, chunks)) == []

    def test_bad_slice_is_reported_with_chunk_id(self) -> None:
        text = "alpha beta gamma"
        chunks = [_chunk("WRONG", 0, 5, "c1")]
        findings = offset_invariant_findings(_doc(text, chunks))
        assert len(findings) == 1
        assert findings[0].code == "span-slice-mismatch"
        assert findings[0].chunk_id == "c1"
        assert findings[0].severity == "error"

    def test_unanchored_chunks_are_skipped_not_failed(self) -> None:
        chunks = [_chunk("no spans here", None, None, "c1")]
        assert offset_invariant_findings(_doc("no spans here", chunks)) == []


class TestCoverage:
    def test_exact_cover_passes(self) -> None:
        text = "alpha beta gamma"
        chunks = [_chunk("alpha beta", 0, 10, "c1"), _chunk("gamma", 11, 16, "c2")]
        assert coverage_findings(_doc(text, chunks)) == []

    def test_whitespace_gap_passes(self) -> None:
        text = "alpha beta gamma"
        chunks = [_chunk("alpha", 0, 5, "c1"), _chunk("beta gamma", 6, 16, "c2")]
        assert coverage_findings(_doc(text, chunks)) == []

    def test_content_gap_is_an_error(self) -> None:
        text = "alpha MISSING beta"
        chunks = [_chunk("alpha", 0, 5, "c1"), _chunk("beta", 14, 18, "c2")]
        findings = coverage_findings(_doc(text, chunks))
        assert len(findings) == 1
        assert findings[0].code == "coverage-gap"
        assert "MISSING" not in findings[0].detail  # no document text in messages

    def test_overlap_is_an_error(self) -> None:
        text = "alpha beta"
        chunks = [_chunk("alpha be", 0, 8, "c1"), _chunk("beta", 6, 10, "c2")]
        findings = coverage_findings(_doc(text, chunks))
        assert [f.code for f in findings] == ["chunk-overlap"]

    def test_unanchored_chunks_skip_coverage(self) -> None:
        chunks = [_chunk("text", None, None, "c1")]
        assert coverage_findings(_doc("text", chunks)) == []

    def test_empty_document_passes(self) -> None:
        assert coverage_findings(_doc("", [])) == []

    def test_findings_carry_sorted_tiebreakers(self) -> None:
        text = "a X b Y c"
        chunks = [
            _chunk("a", 0, 1, "c2"),
            _chunk("b", 5, 6, "c1"),
        ]
        findings = coverage_findings(_doc(text, chunks))
        assert [f.detail for f in findings] == sorted(f.detail for f in findings)


class TestBudget:
    def test_within_budget_passes(self) -> None:
        text = "short body"
        chunks = [_chunk("short body", 0, 10, "c1")]
        assert budget_findings(_doc(text, chunks)) == []

    def test_oversize_chunk_is_an_error(self) -> None:
        from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS

        big = "x" * (TOKEN_BUDGET_CHARS + 1)
        chunks = [_chunk(big, 0, len(big), "c-big")]
        findings = budget_findings(_doc(big, chunks))
        assert len(findings) == 1
        assert findings[0].code == "budget-exceeded"
        assert findings[0].chunk_id == "c-big"


class TestRunValidators:
    def test_fixed_validator_order(self) -> None:
        text = "alpha beta"
        # Overlap + budget problems at once: offset runs first, then
        # coverage, then budget.
        big = "y" * 99999
        chunks = [
            _chunk(text + big, 0, len(text) + len(big), "c1"),
            _chunk("beta", 6, 10, "c2"),
        ]
        findings = run_validators(_doc(text + big, chunks))
        assert [f.validator for f in findings] == sorted(
            [f.validator for f in findings], key=["offset_invariant", "coverage", "budget"].index
        )

    def test_clean_document_has_no_findings(self) -> None:
        text = "alpha beta gamma"
        chunks = [_chunk("alpha beta", 0, 10, "c1"), _chunk("gamma", 11, 16, "c2")]
        assert run_validators(_doc(text, chunks)) == []

    def test_summary_shape_is_stable(self) -> None:
        from corpus_kb.tools.pipeline.validate import summarize

        text = "alpha MISSING beta"
        chunks = [_chunk("alpha", 0, 5, "c1"), _chunk("beta", 14, 18, "c2")]
        report = summarize("advisory", run_validators(_doc(text, chunks)))
        assert report["mode"] == "advisory"
        assert report["status"] == "findings"
        assert report["problem_count"] == 1
        assert [v["name"] for v in report["validators"]] == [
            "offset_invariant",
            "coverage",
            "budget",
        ]
        # Deterministic: the same input twice gives the same JSON.
        import json

        again = summarize("advisory", run_validators(_doc(text, chunks)))
        assert json.dumps(report, sort_keys=True) == json.dumps(again, sort_keys=True)
