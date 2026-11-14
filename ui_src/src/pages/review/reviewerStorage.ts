// Persisted non-sensitive UI state (F10): the reviewer name only, under
// the corpus-kb: prefix. The key carries its version (React rule 4.4).
// Private mode just does not persist the name.

const REVIEWER_KEY = "corpus-kb:reviewer:v1";
const REVIEWER_V0_KEY = "corpus-kb:reviewer";

/** Carry a name from the pre-version key over once, so the version
    bump never silently forgets a reviewer mid-session. */
function migrateV0(): void {
  const carried = window.localStorage.getItem(REVIEWER_V0_KEY);
  if (carried === null) {
    return;
  }
  if (window.localStorage.getItem(REVIEWER_KEY) === null) {
    window.localStorage.setItem(REVIEWER_KEY, carried);
  }
  window.localStorage.removeItem(REVIEWER_V0_KEY);
}

/** The remembered reviewer name, or "" when none is stored. */
export function loadReviewer(): string {
  try {
    migrateV0();
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
