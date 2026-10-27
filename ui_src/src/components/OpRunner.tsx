import { Button, Input, Typography } from "antd";
import { useMemo, useRef, useState } from "react";
import { callOp } from "../api/client";
import { unknownOpResult } from "../api/errors";
import { getEntry } from "../api/manifest";
import type { OpResult } from "../api/types";
import { strings } from "../strings";
import { useLongOp } from "../hooks/useLongOp";
import { useOp } from "../hooks/useOp";
import ConfirmGate from "./ConfirmGate";
import ErrorPanel from "./ErrorPanel";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";

/** Dispatch one settled result to the right panel. */
function ResultView({ result, onRetry }: { result: OpResult; onRetry: () => void }) {
  if (result.kind === "unavailable") {
    return <UnavailablePanel result={result} onRetry={onRetry} />;
  }
  if (result.ok && result.kind !== "network") {
    return <ReceiptCard result={result} />;
  }
  return <ErrorPanel result={result} onRetry={onRetry} />;
}

/**
 * The generic, manifest-driven op runner. Every op runs through this or a
 * typed form; `paramsKnown: false` gives a validated JSON editor (spec D.2).
 * `initialBody` seeds the editor (the advisor pre-fills it); the JSON
 * stays editable, and a new opId or seed re-seeds it.
 */
export default function OpRunner({
  opId,
  onDone,
  initialBody,
}: {
  opId: string;
  onDone?: (result: OpResult) => void;
  initialBody?: unknown;
}) {
  const op = getEntry(opId);
  const seed = useMemo(() => JSON.stringify(initialBody ?? {}, null, 2), [initialBody]);
  const [bodyText, setBodyText] = useState(seed);
  const [parseError, setParseError] = useState("");
  const [seeded, setSeeded] = useState([opId, seed]);
  const bodyRef = useRef<unknown>({});

  // A caller that swaps the op (or the seed) gets a fresh editor; an
  // unchanged seed leaves the user's edits alone. The reset happens
  // during render (the documented derived-state adjustment), so no
  // effect ever fights the user's typing.
  if (seeded[0] !== opId || seeded[1] !== seed) {
    setSeeded([opId, seed]);
    setBodyText(seed);
    setParseError("");
  }

  const simple = useOp(opId);
  const long = useLongOp(opId, (signal) => {
    const current = getEntry(opId);
    return callOp<unknown>(current?.path ?? "", {
      method: current?.method === "GET" ? "GET" : "POST",
      body: bodyRef.current,
      signal,
      timeoutMs: "none",
    });
  });

  if (!op) {
    return <ErrorPanel result={unknownOpResult()} />;
  }

  /** Parse and validate the JSON editor content against the manifest row. */
  function parseBody(): boolean {
    if (!op) {
      return false;
    }
    try {
      const parsed: unknown = JSON.parse(bodyText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setParseError(strings.runnerInvalidJson);
        return false;
      }
      const body = parsed as Record<string, unknown>;
      const missing = op.required.filter((key) => !(key in body));
      if (missing.length > 0) {
        setParseError(strings.runnerMissingKeys.replace("{keys}", missing.join(", ")));
        return false;
      }
      bodyRef.current = body;
      setParseError("");
      return true;
    } catch {
      setParseError(strings.runnerInvalidJson);
      return false;
    }
  }

  async function runSimple(): Promise<void> {
    if (!parseBody()) return;
    const res = await simple.run(bodyRef.current);
    onDone?.(res);
  }

  async function runLong(): Promise<void> {
    if (!parseBody()) return;
    const res = await long.start();
    onDone?.(res);
  }

  const running = long.running || simple.running;
  const settled = long.result ?? simple.result;
  return (
    <div>
      <Typography.Paragraph type="secondary">{strings.runnerParamsHint}</Typography.Paragraph>
      <Input.TextArea
        rows={6}
        value={bodyText}
        onChange={(e) => setBodyText(e.target.value)}
        disabled={running}
      />
      {parseError !== "" && <Typography.Text type="danger">{parseError}</Typography.Text>}
      <div style={{ marginTop: 8 }}>
        {op.longRunning ? (
          <ConfirmGate
            title={strings.confirmLongTitle}
            description={strings.confirmLongBody}
            loading={running}
            onConfirm={() => void runLong()}
          />
        ) : (
          <Button type="primary" loading={simple.running} onClick={() => void runSimple()}>
            {strings.runnerRunLabel}
          </Button>
        )}
      </div>
      {long.running && (
        <div style={{ marginTop: 8 }}>
          <StatusBanner kind="info">
            {strings.runnerRunningElapsed.replace("{sec}", String(long.elapsedSec))}
          </StatusBanner>
          <Button onClick={() => long.cancel()}>{strings.runnerCancelLabel}</Button>
        </div>
      )}
      {long.unknownOutcome && (
        <div style={{ marginTop: 8 }}>
          <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
          <Button onClick={() => void long.checkStatus()}>{strings.runnerCheckStatus}</Button>
        </div>
      )}
      {long.statusResult !== null && (
        <div style={{ marginTop: 8 }}>
          {/* The status-poll ledger answer renders as a plain status line —
              never as the receipt (that stays the op's own response). */}
          <StatusBanner kind="info">
            {long.statusResult.message !== ""
              ? strings.runnerStatusLine.replace("{message}", long.statusResult.message)
              : strings.runnerStatusEmpty}
          </StatusBanner>
        </div>
      )}
      {settled !== null && (
        <div style={{ marginTop: 12 }}>
          {/* A longRunning op retries through the long path too — the
              simple path would cap the wait at the default timeout. */}
          <ResultView
            result={settled}
            onRetry={op.longRunning ? () => void runLong() : () => void runSimple()}
          />
        </div>
      )}
    </div>
  );
}
