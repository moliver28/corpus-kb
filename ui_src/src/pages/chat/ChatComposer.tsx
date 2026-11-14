// The chat composer (Phase-2 Wave 7): one Sender, Enter sends,
// Shift+Enter adds a line (the surfaced behavior), and stop is reachable
// by tab — the abort rides the hook's controller ([D7]: client abort,
// not a stop endpoint).
import { Sender } from "@ant-design/x";
import { useState } from "react";
import { strings } from "../../strings/chat";
import type { StreamFlightView } from "./flightView";
import { flightActivity } from "./flightView";

export default function ChatComposer({
  disabled,
  busy,
  flight,
  onSend,
  onStop,
}: {
  disabled: boolean;
  busy: boolean;
  flight: StreamFlightView | null;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");
  return (
    <div className="ui-chat-composer">
      <Sender
        value={value}
        onChange={(next: string) => setValue(next)}
        placeholder={strings.chatPlaceholder}
        disabled={disabled}
        loading={busy}
        onCancel={onStop}
        onSubmit={(next: string) => {
          setValue("");
          onSend(next);
        }}
        aria-label={strings.chatPlaceholder}
      />
      <span className="ui-chat-hint">{busy && flight !== null ? (flightActivity(flight) ?? strings.chatSendHint) : strings.chatSendHint}</span>
    </div>
  );
}
