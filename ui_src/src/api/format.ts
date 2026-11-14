// Shared pure formatters for server values (one copy, every screen).

/**
 * One Date from the server's timestamp word. The live arms ship
 * "2026-01-05 10:12:00+00:00" (Postgres str(datetime)) and
 * "2026-01-05 10:12:00+00" (DuckDB VARCHAR cast) — both space
 * separated; Safari refuses the space form, so the word crosses to the
 * ISO T form and a bare +HH offset gains its minutes before parsing.
 */
export function parseServerTime(value: string): Date {
  const normalized = value.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  return new Date(normalized);
}

/** One "added" date word from a server timestamp (fixed locale, UTC). */
export function dateText(iso: string): string {
  const when = parseServerTime(iso);
  if (Number.isNaN(when.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: "UTC",
  }).format(when);
}

/** One 0..1 share -> "82%"; a non-number or a non-finite value reads "". */
export function percentText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "";
}

/**
 * One run-ledger "started" cell: the server word's first 19 characters
 * (the second precision of "2026-01-05 10:12:00..."), "-" when the word
 * is too short to carry it.
 */
export function shortTimeText(value: unknown): string {
  return typeof value === "string" && value.length >= 19 ? value.slice(0, 19) : "-";
}
