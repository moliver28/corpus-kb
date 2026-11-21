"""AMR method-card bodies for the surface registry.

Each card teaches the axis walk (the register's selection rules): state
the question, match the output family, check the inputs, budget
calibration and autonomy, read the cautions, run, then attach the
validation and the permitted claims. Axis facts come from
config/analysis_methods.yaml (the register rows for the op), verbatim
in meaning; the docs table is docs/ANALYSIS-METHODS.md. The three
wave-4 UXR method cards (survey open-end codes, usability severity,
emotion tag) live here too, so the registry stays under its size
ceiling. All strings are plain English (ASD-STE100).
"""

from __future__ import annotations

_AMR_WALK = """## The axis walk (before you run)

1. State the research question in one sentence.
2. Match the output family this op serves: {family}.
3. Check the inputs it needs: {inputs}.
4. Budget the calibration effort ({calibration}) and autonomy ({autonomy}).
5. Read the cautions. Run. Then do the validation step.

"""

_AMR_FOOT = """- Cautions: {cautions}
- Validation: {validation}
- Claim only: {claims}
- A guard path (disabled, empty, unavailable) returns a reason and a
  remedy, never a guessed result. Exit codes: 0 answered, 1 failed,
  3 unavailable.
- All flags: `corpus-kb {cli} --help`
- Method rows: docs/ANALYSIS-METHODS.md (the register is the source).
"""


def _amr_body(
    name: str,
    family: str,
    inputs: str,
    calibration: str,
    autonomy: str,
    run: str,
    cautions: str,
    validation: str,
    claims: str,
    difficulty: str | None = None,
    teaches: str | None = None,
    requires: str | None = None,
    next_step: str | None = None,
) -> str:
    """One method card: axis walk + run + cautions (all plain English).

    The optional progression lines (novice-UX program, wave 1) mirror the
    register's difficulty/concepts/prerequisites/next fields; they render
    only when the caller supplies them, so the front-door cards stay bare.
    """
    progression: list[str] = []
    if difficulty:
        progression.append(f"- Difficulty: `{difficulty}`.")
    if teaches:
        progression.append(f"- Teaches: {teaches}.")
    if requires:
        progression.append(f"- Run first: {requires}.")
    if next_step:
        progression.append(f"- Next step: {next_step}.")
    progression_block = (
        "## Progression (the register's ladder)\n\n" + "\n".join(progression) + "\n\n"
        if progression
        else ""
    )
    return (
        f"# corpus-{name} — analysis method card\n\n"
        + _AMR_WALK.format(family=family, inputs=inputs, calibration=calibration, autonomy=autonomy)
        + progression_block
        + f"## Run\n\n  ```\n  corpus-kb {run}\n  ```\n\n"
        + _AMR_FOOT.format(cautions=cautions, validation=validation, claims=claims, cli=name)
    )


_SURVEY_CODES_BODY = _amr_body(
    "survey-openend-codes",
    family="labels (method 31)",
    inputs="raw_text, embeddings",
    calibration="moderate",
    autonomy="human_in_loop",
    run="survey-openend-codes [--chunk-id <id> ...] [--min-cluster-size N]",
    cautions="cluster labels come from exemplar answers. Small but "
    "important groups can hide in the noise. This is not human coding.",
    validation="check a sample of coded answers by hand. Compare the "
    "labels against the raw answers for mixed or sarcastic cases.",
    claims="per-question code labels, counts, and exemplar answers.",
    difficulty="novice",
    teaches="`clustering`, `qualitative_coding`",
    requires="method 24, ask or a survey ingest, so survey answers exist as chunks",
)

_SEVERITY_BODY = _amr_body(
    "usability-severity",
    family="scores (method 32)",
    inputs="raw_text",
    calibration="low",
    autonomy="automated_with_audit",
    run="usability-severity [--chunk-id <id> ...] [--limit N]",
    cautions="anchor words miss indirect wording, and the score reads "
    "wording, not measured impact. Unmatched rows score 0 and route to "
    "review.",
    validation="read a sample of scored findings against the severity "
    "anchors. Recalibrate the anchor word lists per study.",
    claims="rubric-based screening scores under the fixed 0 to 4 anchors.",
    difficulty="novice",
    teaches="`keyword_rules`",
    requires="method 22, analyze_run summaries, so findings exist as chunks",
)

_EMOTION_BODY = _amr_body(
    "emotion-tag",
    family="labels (method 33)",
    inputs="raw_text, llm",
    calibration="moderate",
    autonomy="automated_with_audit",
    run="emotion-tag [--chunk-id <id> ...] [--limit N] [--model <tag>]",
    cautions="zero-shot entailment scores are not established "
    "probabilities. Mixed feelings, irony, and statement wording can "
    "flip the judgments.",
    validation="test the statements against paraphrased, negated, and "
    "mixed-affect records. Check a sample of tags by hand.",
    claims="zero-shot emotion tags under the fixed eight-label set.",
    difficulty="intermediate",
    teaches="`classification`, `sentiment_analysis`",
)
