import { Card } from "antd";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";

/** One honest not-wired line for a coding step whose op row is absent. */
function Line({ opId, text }: { opId: string; text: string }) {
  if (getOp(opId) !== undefined) {
    return null;
  }
  return <StatusBanner kind="warn">{text}</StatusBanner>;
}

/**
 * The coding-pipeline steps whose manifest rows have not landed. Each
 * line upgrades itself (and disappears) when its op joins the manifest.
 */
export default function NotWiredCard() {
  return (
    <Card title={strings.codebookNotWiredTitle}>
      <div className="ui-stack">
        <Line opId="coding-embed-codebook" text={strings.codebookEmbedNotWired} />
        <Line opId="coding-pool" text={strings.codebookPoolNotWired} />
        <Line opId="coding-batch" text={strings.codebookBatchNotWired} />
        <Line opId="coding-calibrate" text={strings.codebookCalibrateNotWired} />
        <Line opId="coding-reliability" text={strings.codebookReliabilityNotWired} />
        <Line opId="coding-saturation" text={strings.codebookSaturationNotWired} />
      </div>
    </Card>
  );
}
