import { Card } from "antd";
import { strings } from "../strings";
import DegradedCard from "../components/DegradedCard";
import StatsStrip from "../components/StatsStrip";
import DocumentsCard from "./review/DocumentsCard";
import ReviewQueueCard from "./review/ReviewQueueCard";
import TopicsCard from "./review/TopicsCard";

/**
 * The Review stage — corpus health (the stats strip), the coding review
 * queue, the document table, and the discovered topics.
 */
export default function Review() {
  return (
    <div className="ui-stack">
      <Card title={strings.reviewTitle}>
        <p>{strings.reviewBody}</p>
      </Card>
      <StatsStrip />
      <DocumentsCard />
      <DegradedCard />
      <ReviewQueueCard />
      <TopicsCard />
    </div>
  );
}
