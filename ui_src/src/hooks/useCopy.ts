// The one copy-to-clipboard hook (shared seam): a 2-second "Copied."
// reset, an honest note when the browser hides the clipboard, and a
// keyed variant for per-row copies. The timer never survives unmount.
import { useEffect, useRef, useState } from "react";

/** How long the copy note stays before it resets. */
export const COPY_FEEDBACK_MS = 2000;

/** One hook instance's copy state, read by the button's label. */
export interface CopyState {
  /** The key of the copy that landed ("self", or a row id). */
  copiedKey: string | null;
  /** True when a copy attempt found no clipboard (or it refused). */
  unavailable: boolean;
  /** Copy text; key tells rows apart ("self" for a single button). */
  copy: (text: string, key?: string) => void;
}

export function useCopy(): CopyState {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const timerRef = useRef<number | null>(null);

  // The note clears itself; the timer never survives us.
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  function resetLater(): void {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      setCopiedKey(null);
      setUnavailable(false);
    }, COPY_FEEDBACK_MS);
  }

  function copy(text: string, key = "self"): void {
    // jsdom and hardened browsers expose no clipboard: the button stays
    // honest (never promises what it cannot do) and never throws.
    const clipboard = (navigator as { clipboard?: Clipboard }).clipboard;
    if (clipboard === undefined) {
      setCopiedKey(null);
      setUnavailable(true);
      resetLater();
      return;
    }
    void clipboard.writeText(text).then(
      () => {
        setUnavailable(false);
        setCopiedKey(key);
        resetLater();
      },
      () => {
        setCopiedKey(null);
        setUnavailable(true);
        resetLater();
      },
    );
  }

  return { copiedKey, unavailable, copy };
}
