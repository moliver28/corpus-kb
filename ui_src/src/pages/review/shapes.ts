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

// The confidence and timestamp cells format through api/format.ts (the
// one shared copy); review keeps only its own row shapes below.

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

// -- Document card shapes (tools/ingest_tools.py list_documents +
//    handlers/tag_handler.py tag and metadata reads). Same drift rule:
//    a payload that misses a field takes the honest fallback path.

/** One corpus document row (the DocumentResult dump of documents-list). */
export interface DocumentRow {
  doc_id: string;
  source: string;
  source_type: string;
  chunk_count: number;
  created_at: string;
}

interface DocumentsEnvelope {
  status: string;
  result: DocumentRow[];
}

export function isDocumentsEnvelope(value: unknown): value is DocumentsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.doc_id === "string" &&
        typeof r.source === "string" &&
        typeof r.source_type === "string" &&
        typeof r.chunk_count === "number" &&
        Number.isFinite(r.chunk_count) &&
        typeof r.created_at === "string"
      );
    })
  );
}

/** One tag row of a document's tag list (name is the only certain field). */
export interface TagRow {
  name: string;
  color: unknown;
}

interface TagsEnvelope {
  status: string;
  result: TagRow[];
}

export function isTagsEnvelope(value: unknown): value is TagsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      return typeof (row as Record<string, unknown>).name === "string";
    })
  );
}

/** One metadata row of a document (key is the only certain field). */
export interface MetadataRow {
  key: string;
  value: unknown;
  doc_id: unknown;
}

interface MetadataEnvelope {
  status: string;
  result: MetadataRow[];
}

export function isMetadataEnvelope(value: unknown): value is MetadataEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      return typeof (row as Record<string, unknown>).key === "string";
    })
  );
}

/** One word for a metadata value: strings pass; anything else is JSON. */
export function metadataText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

/** The server tag color for an antd Tag, or undefined unless it is a hex word. */
export function tagColor(color: unknown): string | undefined {
  return typeof color === "string" && /^#[0-9a-fA-F]{6}$/.test(color) ? color : undefined;
}
