// Persisted non-sensitive UI state (F10): the reviewer name only, under
// the corpus-kb: prefix. Private mode just does not persist the name.

const REVIEWER_KEY = "corpus-kb:reviewer";

/** The remembered reviewer name, or "" when none is stored. */
export function loadReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Remember the reviewer name (a failed write is silent by design). */
export function saveReviewer(name: string): void {
  try {
    window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    // Private mode: the name just does not persist.
  }
}
