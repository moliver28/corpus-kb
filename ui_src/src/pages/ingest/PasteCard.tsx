import { Card } from "antd";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";

/**
 * The paste-text ingest. The server route exists but the op has no
 * manifest row yet, so the tab shows the honest gap banner; it upgrades
 * itself (and this banner disappears) when the row lands.
 */
export default function PasteCard() {
  const wired = getOp("ingest-text") !== undefined;
  return (
    <Card title={strings.ingestTabPaste}>
      {wired ? null : <StatusBanner kind="warn">{strings.ingestPasteNotWired}</StatusBanner>}
    </Card>
  );
}
