import { Card } from "antd";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";

/** One honest not-wired line, rendered only while the op row is absent. */
function Line({ opId, text }: { opId: string; text: string }) {
  if (getOp(opId) !== undefined) {
    return null;
  }
  return <StatusBanner kind="warn">{text}</StatusBanner>;
}

/**
 * The analysis families whose server routes exist but whose manifest
 * rows have not landed. Each line upgrades itself (and disappears) the
 * moment its op joins the manifest.
 */
export default function NotWiredCard() {
  return (
    <Card title={strings.analyzeNotWiredTitle}>
      <div className="ui-stack">
        <Line opId="statements-list" text={strings.analyzeStatementsNotWired} />
        <Line opId="conflicts-scan" text={strings.analyzeConflictsNotWired} />
        <Line opId="beliefs-current" text={strings.analyzeBeliefsNotWired} />
        <Line opId="analysis-embed-meta" text={strings.analyzeEmbedMetaNotWired} />
        <Line opId="extraction-tune" text={strings.analyzeExtractionTuneNotWired} />
      </div>
    </Card>
  );
}
