// The ONE hash-query reader/writer for view state (tabs, filters). The
// app routes through HashRouter, so the query rides the hash (`#/ingest?
// tab=queue`) and survives reloads and links. Pages pass their known
// value list; an absent or unknown value reads as null, so every page
// keeps its honest default and an unknown word can never crash a page.
import { useCallback } from "react";
import { useSearchParams } from "react-router";

/**
 * Read `key` from the location query (null when absent or not in
 * `allowed`), and write it back in place. Writing null removes the key,
 * and so does writing the `fallback` value when one is given — the URL
 * carries only non-default state. The write replaces (no history entry
 * per tab click).
 */
export function useQueryParam<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback?: T,
): [T | null, (value: T | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);
  const value = raw !== null && (allowed as readonly string[]).includes(raw) ? (raw as T) : null;
  const setValue = useCallback(
    (next: T | null): void => {
      setParams(
        (prev) => {
          const copy = new URLSearchParams(prev);
          if (next === null || next === fallback) {
            copy.delete(key);
          } else {
            copy.set(key, next);
          }
          return copy;
        },
        { replace: true },
      );
    },
    [key, fallback, setParams],
  );
  return [value, setValue];
}
