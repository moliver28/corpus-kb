import { Card } from "antd";
import { strings } from "../strings";
import DegradedCard from "../components/DegradedCard";
import DocumentsCard from "./review/DocumentsCard";
import ReviewQueueCard from "./review/ReviewQueueCard";
import TopicsCard from "./review/TopicsCard";

/**
 * The Review stage — corpus health, the coding review queue, and the
 * discovered topics. The document table waits for its manifest read op.
 */
export default function Review() {
  return (
    <div className="ui-stack">
      <Card title={strings.reviewTitle}>
        <p>{strings.reviewBody}</p>
      </Card>
      <DocumentsCard />
      <DegradedCard />
      <ReviewQueueCard />
      <TopicsCard />
    </div>
  );
}
