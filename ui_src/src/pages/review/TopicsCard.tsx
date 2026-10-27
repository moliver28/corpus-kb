import { Card, Skeleton } from "antd";
import type { ReactNode } from "react";
import { useEffect, useRef } from "react";
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

  // One GET on mount through a ref (useOp's run is not stable).
  const runRef = useRef<() => Promise<unknown>>(topics.run);
  useEffect(() => {
    void runRef.current();
  }, []);

  function refresh(): void {
    topics.reset();
    void topics.run();
  }

  let body: ReactNode;
  const result = topics.result;
  if (result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 3 }} />;
  } else if (result.kind === "network") {
    body = <ErrorPanel result={result} onRetry={refresh} />;
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
