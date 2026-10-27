import { Card, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { runtimeConfig } from "../api/runtimeConfig";
import DegradedCard from "../components/DegradedCard";
import DirectoryCard from "../components/DirectoryCard";
import JsonViewer from "../components/JsonViewer";
import StatusBanner from "../components/StatusBanner";
import { strings } from "../strings";
import ResearchIngestCard from "./scale/ResearchIngestCard";
import { useLedgerPoll } from "./scale/useLedgerPoll";

/** The self-polling run ledger (spec F6): poll_ms, hidden pause, backoff. */
function LedgerCard() {
  const poll = useLedgerPoll();
  let body: ReactNode;
  if (poll.result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 3 }} />;
  } else {
    body = <JsonViewer value={poll.result.data} />;
  }
  const pollSec = Math.round(runtimeConfig().pollMs / 1000);
  return (
    <Card title={strings.scaleLedgerTitle}>
      <Typography.Paragraph type="secondary">{strings.scaleLedgerBody}</Typography.Paragraph>
      {poll.paused ? (
        <StatusBanner kind="info">{strings.scaleLedgerPaused}</StatusBanner>
      ) : poll.backingOff ? (
        <StatusBanner kind="warn">{strings.scaleLedgerBackoff}</StatusBanner>
      ) : (
        <StatusBanner kind="info">
          {strings.scaleLedgerPolling.replace("{sec}", String(pollSec))}
        </StatusBanner>
      )}
      {body}
    </Card>
  );
}

/**
 * The Scale stage — the run ledger, the bulk folder walk, the research
 * ingest with its force warning, and the re-embed watch.
 */
export default function Scale() {
  return (
    <div className="ui-stack">
      <Card title={strings.scaleTitle}>
        <p>{strings.scaleBody}</p>
      </Card>
      <LedgerCard />
      <DirectoryCard title={strings.scaleBulkTitle} />
      <ResearchIngestCard />
      <DegradedCard />
    </div>
  );
}
