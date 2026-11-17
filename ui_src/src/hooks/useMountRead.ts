// One read at mount time: the effect ten cards used to hand-roll.
import { useEffect, useRef } from "react";

/**
 * Fire one read at mount time. The read runs once after the
 * component's first render. A re-render with a fresh closure never
 * re-fires it, so callers may pass an inline arrow. Pass a bigger
 * `bump` number to fire the latest closure again (a fresh write makes
 * new rows pickable). The caller keeps its own useOp state; this hook
 * owns only the fire-once story and its stable-identity comment.
 */
export function useMountRead(read: () => void, bump?: number): void {
  // The ref update rides its own effect (never a render write), and it
  // runs before the read effect below, so the latest closure fires.
  const readRef = useRef(read);
  useEffect(() => {
    readRef.current = read;
  });
  useEffect(() => {
    readRef.current();
  }, [bump]);
}
