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
   * One write: the body goes to the op path (path slots included). A
   * press while a write is already on the wire returns false and sends
   * nothing (the Enter path skips the button's loading gate). On ok the
   * caller's refresh runs and the banner names the save; on error the
   * message lands in `error` and the first control marked aria-invalid
   * inside rootRef takes focus.
   */
  submit(body: unknown, opts?: { pathParams?: Record<string, string> }): Promise<boolean>;
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
      if (running) {
        return false; // one write on the wire; the re-entry sends nothing
      }
      setSaved(null);
      setError(null);
      const result = await run(body, undefined, opts?.pathParams);
      if (result.ok) {
        setSaved(savedMessage);
        refresh();
        return true;
      }
      setError(result.message !== "" ? result.message : result.status);
      flag();
      return false;
    },
    [run, running, savedMessage, refresh, flag],
  );

  return { running, error, saved, rootRef, submit };
}
