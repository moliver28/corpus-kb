// One shared view type for the in-flight turn (Phase-2 Wave 7): what
// the panel renders while one SSE stream runs. Kept separate so the
// panel test and the hook test agree on the shape without importing
// the hook's state machinery.
import { strings } from "../../strings/chat";

export interface StreamFlightView {
  text: string;
  activity: string | null;
  warning: string | null;
}

/** The waiting line: the server's activity, else the searching words. */
export function flightActivity(flight: StreamFlightView): string | null {
  return flight.activity ?? (flight.text === "" ? strings.chatActivitySearching : null);
}
