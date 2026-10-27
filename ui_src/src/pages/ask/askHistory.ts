// Client-only ask history (F7): the newest questions of ONE browser
// session, kept in sessionStorage under the corpus-kb: prefix. Nothing
// goes to the server; a closed tab forgets the list.
const KEY = "corpus-kb:ask-history";
const CAP = 10;

export interface HistoryEntry {
  question: string;
  at: string;
}

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry): entry is HistoryEntry =>
        typeof entry === "object" &&
        entry !== null &&
        typeof (entry as HistoryEntry).question === "string" &&
        typeof (entry as HistoryEntry).at === "string",
    );
  } catch {
    return [];
  }
}

export function saveHistory(entries: HistoryEntry[]): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(entries.slice(0, CAP)));
  } catch {
    // Private mode: the list just does not persist.
  }
}
