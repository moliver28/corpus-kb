// The one form-to-write seam: values go in, one manifest write goes out,
// and the row/list re-read follows an ok answer. A typed failure lands
// inline under the field that owns it, and the first invalid field takes
// focus. Extracted from the document drawer's write mode (Phase-2 Wave
// 2); Waves 4+ reuse it for the graph forms. Do not generalize beyond
// "one form, one op, one refresh" without a second real consumer.
import { useCallback, useState } from "react";
import type { RefObject } from "react";
import { useFirstErrorFocus } from "../components/LabeledField";
import { useOp } from "./useOp";

/** The controls and state one form-to-write needs. */
export interface FormWrite {
  /** True while the write is on the wire. */
  running: boolean;
  /** The typed failure word; render it under the field that owns it. */
  error: string | null;
  /** The success word after an ok write; render it in an ok banner. */
  saved: string | null;
  /** Attach to the element that wraps every field of the form. */
  rootRef: RefObject<HTMLDivElement | null>;
  /**
   * One write: the body goes to the op path (path slots and query pairs
   * included). On ok the caller's refresh runs and the banner names the
   * save; on error the message lands in `error` and the first control
   * marked aria-invalid inside rootRef takes focus.
   */
  submit(
    body: unknown,
    opts?: { query?: Record<string, string>; pathParams?: Record<string, string> },
  ): Promise<boolean>;
  /** Drop the banner and the inline error (the caller edits again). */
  clear(): void;
}

export function useFormWrite(
  opId: string,
  savedMessage: string,
  refresh: () => void,
): FormWrite {
  const { run, running } = useOp(opId);
  const { rootRef, flag } = useFirstErrorFocus();
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const submit = useCallback<FormWrite["submit"]>(
    async (body, opts) => {
      setSaved(null);
      setError(null);
      const result = await run(body, opts?.query, opts?.pathParams);
      if (result.ok) {
        setSaved(savedMessage);
        refresh();
        return true;
      }
      setError(result.message !== "" ? result.message : result.status);
      flag();
      return false;
    },
    [run, savedMessage, refresh, flag],
  );

  const clear = useCallback(() => {
    setSaved(null);
    setError(null);
  }, []);

  return { running, error, saved, rootRef, submit, clear };
}
