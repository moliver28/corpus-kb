// Shapes for the Ask screen reads. The SPA reads only these fields;
// anything else stays in the JsonViewer. The typeguards keep a drifted
// or unknown payload on the honest fallback path (DoctorCard pattern)
// instead of crashing the screen.
import type { Citation } from "../../components/CitedAnswer";
import type { VerifySummary } from "../../api/verify";
import type { EvidenceItem } from "./EvidenceList";
import { strings } from "../../strings";

/** One cited answer sentence (research/notebook.py cite_sentences). */
export interface Sentence {
  text: string;
  citations: number[];
}

/** One numbered evidence block (ask/composer.py evidence_blocks). */
export interface SourceBlock {
  n: number;
  source: string;
  text: string;
}

/** The inner result of the ask envelope (AnswerBundle.to_payload). */
export interface AskResult {
  answer: string;
  sentences: Sentence[];
  sources: SourceBlock[];
  verify: unknown;
  note: unknown;
}

export interface AskEnvelope {
  status: string;
  result: AskResult;
  degraded_excluded: { count: unknown };
}

function isSentence(value: unknown): value is Sentence {
  if (typeof value !== "object" || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    typeof s.text === "string" &&
    Array.isArray(s.citations) &&
    s.citations.every((n) => typeof n === "number")
  );
}

function isSource(value: unknown): value is SourceBlock {
  if (typeof value !== "object" || value === null) return false;
  const s = value as Record<string, unknown>;
  return typeof s.n === "number" && typeof s.source === "string" && typeof s.text === "string";
}

export function isAskEnvelope(value: unknown): value is AskEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  if (typeof v.result !== "object" || v.result === null) return false;
  const r = v.result as Record<string, unknown>;
  return (
    typeof r.answer === "string" &&
    Array.isArray(r.sentences) &&
    r.sentences.every(isSentence) &&
    Array.isArray(r.sources) &&
    r.sources.every(isSource)
  );
}

/** The verify block, or null when the server sent nothing readable. */
export function verifyOf(env: AskEnvelope): VerifySummary | null {
  return typeof env.result.verify === "object" && env.result.verify !== null
    ? (env.result.verify as VerifySummary)
    : null;
}

/** The server note, or "" when absent. */
export function noteOf(env: AskEnvelope): string {
  return typeof env.result.note === "string" ? env.result.note : "";
}

/** The honest count of chunks the impaired index left out. */
export function excludedCount(block: { count: unknown } | null | undefined): number {
  const count = block?.count;
  return typeof count === "number" && Number.isFinite(count) ? count : 0;
}

/** Map the answer sentences onto CitedAnswer citations (source labels). */
export function toCitations(env: AskEnvelope): Citation[] {
  const sources = env.result.sources;
  return env.result.sentences.map((sentence) => {
    const labels = [
      ...new Set(
        sentence.citations
          .map((n) => sources[n - 1]?.source ?? "")
          .filter((label) => label !== ""),
      ),
    ];
    return { text: sentence.text, source: labels.join(", "), sources: labels };
  });
}

/** The evidence list under an honest empty or degraded answer. */
export function askEvidence(env: AskEnvelope): EvidenceItem[] {
  return env.result.sources.map((source) => ({ text: source.text, source: source.source }));
}

/** One search result row (handlers/query_integrity.py envelope). */
export interface SearchRow {
  chunk_id: string;
  source: string;
  text: string;
  score: unknown;
}

export interface SearchEnvelope {
  status: string;
  result: SearchRow[];
  degraded_excluded: { count: unknown };
}

export function isSearchEnvelope(value: unknown): value is SearchEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  return Array.isArray(v.result) && v.result.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return typeof r.chunk_id === "string" && typeof r.source === "string" && typeof r.text === "string";
  });
}

/** One score cell -> "0.91" (a number cell only; anything else reads ""). */
export function scoreText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "";
}

/** One notebook evidence row (research/notebook.py enrich_citations). */
export interface NotebookEvidenceRow {
  n: number;
  title: unknown;
  speaker: unknown;
  timestamp_s: unknown;
  answer: unknown;
  question: unknown;
}

export interface NotebookEnvelope {
  status: string;
  sentences: Sentence[];
  evidence: NotebookEvidenceRow[];
  note: unknown;
  message: unknown;
}

export function isNotebookEnvelope(value: unknown): value is NotebookEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  if ("sentences" in v && (!Array.isArray(v.sentences) || !v.sentences.every(isSentence))) {
    return false;
  }
  if ("evidence" in v && !Array.isArray(v.evidence)) return false;
  return true;
}

function notebookRows(env: NotebookEnvelope): NotebookEvidenceRow[] {
  return Array.isArray(env.evidence)
    ? env.evidence.filter((row): row is NotebookEvidenceRow => typeof row?.n === "number")
    : [];
}

/** One evidence row's locator: title, speaker, and a time or nothing. */
function locator(row: NotebookEvidenceRow): string {
  const parts = [
    typeof row.title === "string" && row.title !== ""
      ? row.title
      : strings.askEvidenceUntitled,
  ];
  if (typeof row.speaker === "string" && row.speaker !== "") parts.push(row.speaker);
  if (typeof row.timestamp_s === "number") {
    parts.push(strings.askEvidenceAt.replace("{sec}", String(Math.round(row.timestamp_s))));
  }
  return parts.join(" - ");
}

/** The ranked evidence list (answer text, else the question text). */
export function notebookEvidence(env: NotebookEnvelope): EvidenceItem[] {
  return notebookRows(env).map((row) => ({
    text:
      typeof row.answer === "string" && row.answer !== ""
        ? row.answer
        : typeof row.question === "string"
          ? row.question
          : "",
    source: locator(row),
  }));
}

/** The notebook's cited sentences, each pointing at its evidence rows. */
export function notebookSentences(env: NotebookEnvelope): Citation[] {
  const rows = notebookRows(env);
  return env.sentences.map((sentence) => ({
    text: sentence.text,
    source: sentence.citations
      .map((n) => {
        const row = rows.find((candidate) => candidate.n === n);
        return row === undefined ? "" : locator(row);
      })
      .filter((label) => label !== "")
      .join(", "),
  }));
}
