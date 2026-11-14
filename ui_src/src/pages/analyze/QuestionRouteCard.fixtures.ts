import { fireEvent, screen } from "@testing-library/react";
import { strings } from "../../strings/analyze";
import { calls, respond, stubFetch, stubFetchImpl } from "../../test/testUtils";

/**
 * Shared fixtures and fetch stubs for the QuestionRouteCard tests
 * (split out of QuestionRouteCard.test.tsx for the 250-line rule).
 *
 * The fixtures cover the card's canned envelopes: the resolved tile with
 * the always-visible one-line why, the read-only ambiguity ladder, the
 * degraded status that still shows its ladder (the addendum grammar),
 * the collapsed trace, and the override select that re-calls the op
 * with the chosen method_id.
 */

export const RESOLVED: Record<string, unknown> = {
  status: "resolved",
  intent: {
    question: "how do i find duplicate records?",
    status: "resolved",
    confidence: 0.9,
    method_id: "29",
    note: null,
    readings: [],
  },
  ranked: [
    {
      method_id: "29",
      name: "Near-duplicate and paraphrase detection",
      why: "Find duplicate or paraphrased records before analysis.",
      run_hint: { op: "duplicates_screen", params: {} },
    },
    {
      method_id: "04",
      name: "Dictionary or rule-based classification",
      why: "All axes are equal. The id order decides.",
      run_hint: { op: null, params: {} },
    },
    {
      method_id: "12",
      name: "Cluster structure inspector",
      why: "The rank rides the runner-up.",
      run_hint: { op: "cluster_texts", params: {} },
    },
  ],
  trace: {
    mode: "phrase",
    threshold: 0.55,
    margin: 0.1,
    scores: [{ method_id: "29", score: 0.9 }],
  },
  suggestions: [
    "Which near duplicates are genuinely separate speakers?",
    "How do thresholds split review from merge?",
    "What paraphrases survived the exact-match pass?",
  ],
};

export const READING_A = {
  method_id: "05",
  method_name: "Cluster method",
  why: "How do I cluster my documents by theme?",
};
export const READING_B = {
  method_id: "01",
  method_name: "Codebook method",
  why: "How do I build a codebook for my interviews?",
};

export const AMBIGUOUS: Record<string, unknown> = {
  status: "ambiguous",
  intent: { status: "ambiguous", note: null, method_id: null },
  ranked: [
    {
      method_id: "05",
      name: "Cluster method",
      why: READING_A.why,
      run_hint: { op: null, params: {} },
    },
    {
      method_id: "01",
      name: "Codebook method",
      why: READING_B.why,
      run_hint: { op: null, params: {} },
    },
  ],
  interpretations: [READING_A, READING_B],
  trace: { mode: "phrase", threshold: 0.55, margin: 0.1, scores: [] },
};

/** Degraded envelope with an ambiguous intent: the ladder must stay. */
export const DEGRADED_AMBIGUOUS: Record<string, unknown> = {
  ...AMBIGUOUS,
  status: "degraded",
  intent: { status: "ambiguous", note: "degraded", method_id: null },
};

/** The override answer a picked reading routes to (the pick's method_id). */
export const OVERRIDE_05: Record<string, unknown> = {
  status: "resolved",
  intent: {
    question: "how do i group and code these records?",
    status: "resolved",
    confidence: 0.0,
    method_id: "05",
    note: "expert_override",
    readings: [],
    ranked_method_ids: ["05", "01"],
  },
  ranked: [
    {
      method_id: "05",
      name: "Cluster method",
      why: "Set by the analyst.",
      run_hint: { op: "cluster_texts", params: {} },
    },
    {
      method_id: "01",
      name: "Codebook method",
      why: READING_B.why,
      run_hint: { op: null, params: {} },
    },
  ],
  suggestions: ["Which cluster count fits these interviews?"],
  trace: { mode: "phrase", threshold: 0.55, margin: 0.1, scores: [] },
};

/** Thirty minimal register rows (the override select's source). */
function methodList(count = 30): Record<string, unknown> {
  return {
    status: "success",
    register_version: "1.1.0",
    recommendations: Array.from({ length: count }, (_, index) => ({
      id: String(index + 1).padStart(2, "0"),
      name: `Method ${index + 1}`,
      summary: "A test row.",
      complexity_tier: "baseline",
      rank: index + 1,
      rank_reason: "Test order.",
      op: null,
    })),
  };
}

export function stubBoth(routeBody: Record<string, unknown>): void {
  stubFetch([
    { method: "POST", path: "/api/analysis/route", reply: respond(200, routeBody) },
    { method: "POST", path: "/api/analysis/select-method", reply: respond(200, methodList()) },
  ]);
}

/** First route post answers `first`, later ones answer `second` (the
    pick's re-route lands as a different envelope shape). */
export function stubSequential(
  first: Record<string, unknown>,
  second: Record<string, unknown>,
): void {
  let routePosts = 0;
  stubFetchImpl((url: string | URL, init?: { method?: string; body?: unknown }) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const path = String(url);
    const known =
      method === "POST" && (path.includes("/api/analysis/route") || path.includes("/api/analysis/select-method"));
    if (!known) {
      return Promise.reject(new Error(`unexpected fetch: ${method} ${path}`));
    }
    calls.push({ method, url: path, body: String(init?.body ?? "") });
    if (path.includes("/api/analysis/select-method")) {
      return Promise.resolve(respond(200, methodList()));
    }
    routePosts += 1;
    return Promise.resolve(respond(200, routePosts === 1 ? first : second));
  });
}

export function routeCalls(): { url: string; body: string }[] {
  return calls
    .filter((c) => c.method === "POST" && c.url.includes("/api/analysis/route"))
    .map((c) => ({ url: c.url, body: c.body }));
}

export async function ask(question: string): Promise<void> {
  fireEvent.change(await screen.findByPlaceholderText(strings.routeQuestionPlaceholder), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: strings.routeQuestionAction }));
}
