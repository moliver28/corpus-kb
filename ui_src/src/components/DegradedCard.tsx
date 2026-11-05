import { Card, Skeleton } from "antd";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import type { OpResult } from "../api/types";
import ConfirmGate from "./ConfirmGate";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings";

// The payload guards ride with the shared card (a component never
// imports stage code): the ingest-status degraded block and the honest
// re-embed summary, exactly as the server sends them.
interface DegradedEnvelope {
  status: string;
  degraded: { chunks: unknown; chunk_vectors: unknown };
}

function isDegradedEnvelope(value: unknown): value is DegradedEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const d = (value as Record<string, unknown>).degraded;
  return typeof d === "object" && d !== null;
}

interface ReembedEnvelope {
  status: string;
  scanned: number;
  fixed: number;
  failed: number;
}

function isReembedEnvelope(value: unknown): value is ReembedEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.scanned === "number" && typeof r.fixed === "number" && typeof r.failed === "number"
  );
}

/** One degraded count cell -> 0 (an absent or junk cell never inflates). */
function degradedCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/**
 * The degraded-chunks watch: a warn banner with the counts while any
 * chunk lacks a vector, and the re-embed action with its honest
 * scanned/fixed/failed summary. With a `ledger` prop the caller owns
 * the ingest-status read (the Scale poll); without one the card does
 * its own single read on mount (the Review stage).
 */
export default function DegradedCard({
  ledger,
  refreshLedger,
}: {
  /** The caller's ledger answer; undefined = the card reads it itself. */
  ledger?: OpResult<unknown> | null;
  /** Re-reads the caller's ledger (after the re-embed, on retry). */
  refreshLedger?: () => void;
} = {}) {
  const status = useOp("ingest-status");
  const reembed = useOp("ingest-reembed");
  const { run: statusRun } = status;
  const shared = ledger !== undefined;
  const [summary, setSummary] = useState<ReembedSummary | null>(null);

  // One GET on mount (own-read mode only). useOp's run holds a stable
  // identity, so the [statusRun] dependency never re-fires the effect.
  useEffect(() => {
    if (shared) {
      return;
    }
    void statusRun();
  }, [shared, statusRun]);

  function refresh(): void {
    if (shared) {
      refreshLedger?.();
      return;
    }
    status.reset();
    void status.run();
  }

  async function reembedNow(): Promise<void> {
    reembed.reset();
    const res = await reembed.run({});
    if (res.ok && isReembedEnvelope(res.data)) {
      setSummary({
        scanned: res.data.scanned,
        fixed: res.data.fixed,
        failed: res.data.failed,
      });
    }
    refresh();
  }

  const result = shared ? ledger : status.result;
  let body: ReactNode;
  if (result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 2 }} />;
  } else if (result.kind === "network") {
    body = <ErrorPanel result={result} onRetry={refresh} opId="ingest-status" />;
  } else if (result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={refresh} />;
  } else if (!isDegradedEnvelope(result.data)) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  } else {
    const degraded = result.data.degraded;
    const total = degradedCount(degraded.chunks) + degradedCount(degraded.chunk_vectors);
    body =
      total === 0 ? (
        <StatusBanner kind="ok">{strings.reviewDegradedNone}</StatusBanner>
      ) : (
        <div className="ui-stack">
          <StatusBanner kind="warn">
            {strings.reviewDegradedCounts
              .replace("{chunks}", String(degradedCount(degraded.chunks)))
              .replace("{vectors}", String(degradedCount(degraded.chunk_vectors)))}
          </StatusBanner>
          <div className="ui-actions">
            {/* A bulk mutation never starts bare: the gate names the
                counts it would rewrite, and the run starts on confirm. */}
            <ConfirmGate
              title={strings.reviewDegradedConfirmTitle}
              description={strings.reviewDegradedConfirmBody
                .replace("{chunks}", String(degradedCount(degraded.chunks)))
                .replace("{vectors}", String(degradedCount(degraded.chunk_vectors)))}
              confirmLabel={strings.reviewDegradedAction}
              loading={reembed.running}
              onConfirm={() => void reembedNow()}
            />
          </div>
          {summary !== null && (
            <StatusBanner kind="ok">
              {strings.reviewDegradedDone
                .replace("{scanned}", String(summary.scanned))
                .replace("{fixed}", String(summary.fixed))
                .replace("{failed}", String(summary.failed))}
            </StatusBanner>
          )}
          {reembed.result !== null && !reembed.result.ok && (
            <ErrorPanel result={reembed.result} onRetry={() => void reembedNow()} opId="ingest-reembed" />
          )}
        </div>
      );
  }

  return <Card title={strings.reviewDegradedTitle}>{body}</Card>;
}

interface ReembedSummary {
  scanned: number;
  fixed: number;
  failed: number;
}
