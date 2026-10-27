import { Button, Card, Skeleton } from "antd";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import {
  degradedCount,
  isDegradedEnvelope,
  isReembedEnvelope,
} from "./shapes";

/**
 * The degraded-chunks watch: one ingest-status read on mount, a warn
 * banner with the counts while any chunk lacks a vector, and the
 * re-embed action with its honest scanned/fixed/failed summary.
 */
export default function DegradedCard() {
  const status = useOp("ingest-status");
  const reembed = useOp("ingest-reembed");
  const [summary, setSummary] = useState<ReembedSummary | null>(null);

  // useOp's run is not referentially stable, so the mount call goes
  // through a ref: one GET on mount, never a re-fetch loop.
  const runRef = useRef<() => Promise<unknown>>(status.run);
  useEffect(() => {
    void runRef.current();
  }, []);

  function refresh(): void {
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

  let body: ReactNode;
  if (status.result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 2 }} />;
  } else if (status.result.kind === "network") {
    body = <ErrorPanel result={status.result} onRetry={refresh} />;
  } else if (status.result.kind === "unavailable") {
    body = <UnavailablePanel result={status.result} onRetry={refresh} />;
  } else if (!isDegradedEnvelope(status.result.data)) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={status.result.data} />
      </div>
    );
  } else {
    const degraded = status.result.data.degraded;
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
            <Button
              type="primary"
              loading={reembed.running}
              onClick={() => void reembedNow()}
            >
              {strings.reviewDegradedAction}
            </Button>
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
            <ErrorPanel result={reembed.result} onRetry={() => void reembedNow()} />
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
