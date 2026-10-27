import { Card } from "antd";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";

/**
 * The corpus document list. The read op (documents-list, GET /api/documents)
 * has no manifest row yet, so the card shows the honest gap banner instead
 * of a table that could never load. When the op lands, this card upgrades
 * to the real table behind the same manifest check.
 */
export default function DocumentsCard() {
  const wired = getOp("documents-list") !== undefined;
  return (
    <Card title={strings.reviewDocsTitle}>
      {wired ? null : <StatusBanner kind="warn">{strings.reviewDocsNotWired}</StatusBanner>}
    </Card>
  );
}
