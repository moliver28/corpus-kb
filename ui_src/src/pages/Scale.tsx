import { Card, Select, Skeleton, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useState } from "react";
import { runtimeConfig } from "../api/runtimeConfig";
import DegradedCard from "../components/DegradedCard";
import DirectoryCard from "../components/DirectoryCard";
import JsonViewer from "../components/JsonViewer";
import ResearchIngestCard from "../components/ResearchIngestCard";
import StatusBanner from "../components/StatusBanner";
import { strings } from "../strings";
import { filterRuns, projectOptions, runsOf } from "./scale/runFilter";
import type { LedgerRun } from "./scale/runFilter";
import { useLedgerPoll } from "./scale/useLedgerPoll";

/** The project filter plus the narrowed run list (migration 036 link). */
function RunFilterView({ runs }: { runs: LedgerRun[] }) {
  const [project, setProject] = useState<string | null>(null);
  const options = projectOptions(runs);
  const visible = filterRuns(runs, project);
  const columns: ColumnsType<LedgerRun> = [
    {
      title: strings.scaleRunsColumnStarted,
      dataIndex: "started_at",
      render: (value: unknown) => short(value),
    },
    {
      title: strings.scaleRunsColumnSource,
      dataIndex: "source_type",
      render: (value: unknown) => dash(value),
    },
    { title: strings.scaleRunsColumnStatus, dataIndex: "status" },
    {
      title: strings.scaleRunsColumnProject,
      dataIndex: "project_id",
      render: (value: unknown) => dash(value),
    },
  ];
  if (runs.length === 0) {
    return null;
  }
  return (
    <div className="ui-stack">
      <Select<string | null>
        aria-label={strings.scaleRunsFilterLabel}
        value={project}
        style={{ minWidth: 220 }}
        onChange={(value) => setProject(value)}
        options={[
          { value: null, label: strings.scaleRunsAll },
          ...options.map((id) => ({ value: id, label: id })),
        ]}
      />
      {visible.length === 0 ? (
        <StatusBanner kind="info">{strings.scaleRunsEmpty}</StatusBanner>
      ) : (
        <Table<LedgerRun>
          size="small"
          rowKey="run_id"
          pagination={false}
          dataSource={visible}
          columns={columns}
        />
      )}
    </div>
  );
}

function short(value: unknown): string {
  return typeof value === "string" && value.length >= 19 ? value.slice(0, 19) : "-";
}

function dash(value: unknown): string {
  return typeof value === "string" && value !== "" ? value : "-";
}

/** The self-polling run ledger (spec F6): poll_ms, hidden pause, backoff. */
function LedgerCard() {
  const poll = useLedgerPoll();
  const runs = poll.result === null ? [] : runsOf(poll.result.data);
  let body: ReactNode;
  if (poll.result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 3 }} />;
  } else {
    body = (
      <div className="ui-stack">
        <RunFilterView runs={runs} />
        <JsonViewer value={poll.result.data} />
      </div>
    );
  }
  const pollSec = Math.round(runtimeConfig().pollMs / 1000);
  return (
    <Card title={strings.scaleLedgerTitle}>
      <Typography.Paragraph type="secondary">{strings.scaleLedgerBody}</Typography.Paragraph>
      {poll.paused ? (
        <StatusBanner kind="info">{strings.scaleLedgerPaused}</StatusBanner>
      ) : poll.backingOff ? (
        <div className="ui-stack">
          <StatusBanner kind="warn">{strings.scaleLedgerBackoff}</StatusBanner>
          {poll.result !== null && poll.result.message !== "" && (
            <StatusBanner kind="info">{poll.result.message}</StatusBanner>
          )}
        </div>
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
