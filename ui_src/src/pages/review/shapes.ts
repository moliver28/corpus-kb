// Shapes for the Review screen reads. The SPA reads only these fields;
// anything else stays in the JsonViewer. The typeguards keep a drifted
// or unknown payload on the honest fallback path (DoctorCard pattern)
// instead of crashing the screen.
import type { TopicRow } from "../../components/TopicsTable";
import { strings } from "../../strings";

/** One coding assignment row (tools/lifecycle_read_tools.py). */
export interface AssignmentRow {
  assignment_id: number;
  assignment_aggregate_id: string;
  unit_id: number;
  code_id: string;
  status: string;
  confidence: unknown;
  created_at: string | null;
}

interface AssignmentsEnvelope {
  status: string;
  result: AssignmentRow[];
}

export function isAssignmentsEnvelope(value: unknown): value is AssignmentsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.assignment_id === "number" &&
        typeof r.assignment_aggregate_id === "string" &&
        typeof r.unit_id === "number" &&
        typeof r.code_id === "string" &&
        typeof r.status === "string"
      );
    })
  );
}

// The server's review-status enum words never render raw: each maps to
// one plain word; anything unknown reads as unknown.
const STATE_WORDS: Record<string, string> = {
  auto: strings.reviewStateAuto,
  review: strings.reviewStateNeedsReview,
  confirmed: strings.reviewStateConfirmed,
  overridden: strings.reviewStateOverridden,
};

/** One plain word for a server review-status value. */
export function reviewStateWord(status: string): string {
  return STATE_WORDS[status] ?? strings.reviewStateUnknown;
}

/** One confidence cell -> "82%" (a number cell only; anything else reads ""). */
export function confidenceText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "";
}

/** One topics row (topics/writer.py read_topics). */
interface TopicPayload {
  topic_id: string;
  label: unknown;
  size: unknown;
  keywords: unknown;
}

interface TopicsEnvelope {
  status: string;
  topics: TopicPayload[];
}

export function isTopicsEnvelope(value: unknown): value is TopicsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (!Array.isArray(v.topics)) return false;
  return v.topics.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    return typeof (row as Record<string, unknown>).topic_id === "string";
  });
}

/** Map the server topics payload onto the shared TopicsTable rows. */
export function topicRows(topics: TopicPayload[]): TopicRow[] {
  return topics.map((topic) => ({
    id: topic.topic_id,
    label: typeof topic.label === "string" ? topic.label : topic.topic_id,
    size: typeof topic.size === "number" ? topic.size : 0,
    keywords: Array.isArray(topic.keywords)
      ? topic.keywords.filter((word): word is string => typeof word === "string")
      : [],
  }));
}
