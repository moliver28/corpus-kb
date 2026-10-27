import { Card } from "antd";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";

/**
 * The verify tab: claim-level groundedness against the cited chunks.
 * The op has no manifest row yet, so the tab shows the honest gap
 * banner (and pulls no runner code). When the row lands, this card
 * wires the real check behind the same manifest look-up.
 */
export default function VerifyCard() {
  const wired = getOp("verify") !== undefined;
  return (
    <Card title={strings.askTabVerify}>
      {wired ? null : (
        <StatusBanner kind="warn">{strings.askVerifyNotWired}</StatusBanner>
      )}
    </Card>
  );
}
