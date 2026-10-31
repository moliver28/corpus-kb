import { Card, Skeleton } from "antd";
import type { ReactNode } from "react";
import { useEffect } from "react";
import TopicsTable from "../../components/TopicsTable";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isTopicsEnvelope, topicRows } from "./shapes";

/**
 * The discovered topics: one topics read on mount, rendered through the
 * shared TopicsTable (label, chunk count, keywords).
 */
export default function TopicsCard() {
  const topics = useOp("topics-list");
  const { run: topicsRun } = topics;

  // One GET on mount. useOp's run holds a stable identity, so the
  // [topicsRun] dependency never re-fires the effect.
  useEffect(() => {
    void topicsRun();
  }, [topicsRun]);

  function refresh(): void {
    topics.reset();
    void topics.run();
  }

  let body: ReactNode;
  const result = topics.result;
  if (result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 3 }} />;
  } else if (result.kind === "network") {
    body = <ErrorPanel result={result} onRetry={refresh} opId="topics-list" />;
  } else if (result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={refresh} />;
  } else if (!isTopicsEnvelope(result.data)) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  } else {
    body = <TopicsTable rows={topicRows(result.data.topics)} />;
  }

  return <Card title={strings.reviewTopicsTitle}>{body}</Card>;
}
