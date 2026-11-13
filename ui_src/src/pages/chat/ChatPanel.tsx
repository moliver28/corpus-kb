// The chat transcript (Phase-2 Wave 7): one Bubble.List over the turns,
// plus the live flight bubble. Roles, labels, and every rendered string
// come from the chat strings table ([D12]); the assistant bubbles carry
// the receipt line (cost, time, model) and the citation chips.
import { Bubble } from "@ant-design/x";
import { Typography } from "antd";
import type { ReactNode } from "react";
import StatusBanner from "../../components/StatusBanner";
import type { ChatTurn } from "../../hooks/useChatSession";
import { strings } from "../../strings/chat";
import type { StreamFlightView } from "./flightView";
import { flightActivity } from "./flightView";
import CitationChips from "./CitationChips";

function receiptLine(turn: ChatTurn): ReactNode {
  const bits: string[] = [];
  if (typeof turn.costUsd === "number") {
    bits.push(strings.chatReceiptCost.replace("{cost}", turn.costUsd.toFixed(4)));
  }
  if (typeof turn.durationMs === "number") {
    bits.push(strings.chatReceiptTime.replace("{seconds}", (turn.durationMs / 1000).toFixed(1)));
  }
  if (typeof turn.numTurns === "number") {
    bits.push(strings.chatReceiptTurns.replace("{count}", String(turn.numTurns)));
  }
  if (turn.model !== undefined && turn.model !== "") {
    bits.push(strings.chatReceiptModel.replace("{model}", turn.model));
  }
  if (bits.length === 0) return null;
  return (
    <Typography.Text type="secondary" className="ui-chat-receipt">
      {bits.join(" · ")}
    </Typography.Text>
  );
}

function assistantContent(turn: ChatTurn): ReactNode {
  return (
    <div className="ui-chat-turn">
      {turn.text !== "" && <p className="ui-chat-turn__text">{turn.text}</p>}
      {turn.stopped === true && <StatusBanner kind="info">{strings.chatStopped}</StatusBanner>}
      {turn.isError === true && <StatusBanner kind="warn">{strings.chatErrorTurn}</StatusBanner>}
      {turn.citations !== undefined && <CitationChips citations={turn.citations} />}
      {receiptLine(turn)}
    </div>
  );
}

function flightContent(flight: StreamFlightView): ReactNode {
  const waiting = flightActivity(flight);
  return (
    <div className="ui-chat-flight">
      {waiting !== null && <StatusBanner kind="info">{waiting}</StatusBanner>}
      {flight.text !== "" && <p className="ui-chat-turn__text">{flight.text}</p>}
      {flight.warning !== null && (
        <StatusBanner kind="warn">{`${strings.chatWarningLabel}: ${flight.warning}`}</StatusBanner>
      )}
    </div>
  );
}

export default function ChatPanel({
  turns,
  flight,
}: {
  turns: ChatTurn[];
  flight: StreamFlightView | null;
}) {
  const items = turns.map((turn, index) => ({
    key: `${turn.role}-${index}`,
    role: turn.role,
    content: turn.role === "assistant" ? assistantContent(turn) : turn.text,
    variant: turn.role === "assistant" ? ("filled" as const) : ("outlined" as const),
    placement: turn.role === "assistant" ? ("start" as const) : ("end" as const),
  }));
  if (flight !== null) {
    items.push({
      key: "flight",
      role: "assistant",
      content: flightContent(flight),
      variant: "filled" as const,
      placement: "start" as const,
    });
  }
  return (
    <div className="ui-chat-panel" role="log" aria-label={strings.chatAriaLog}>
      {items.length === 0 && (
        <Typography.Text type="secondary">{strings.chatBody}</Typography.Text>
      )}
      {items.length > 0 && <Bubble.List items={items} className="ui-chat-bubbles" />}
    </div>
  );
}
