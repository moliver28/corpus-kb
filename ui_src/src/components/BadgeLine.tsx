import { Tag } from "antd";
import { BADGE_THRESHOLDS } from "../api/thresholds.generated";
import { verifyBadge, type BadgeLabel, type VerifySummary } from "../api/verify";
import { strings } from "../strings/app";
import StatusBanner from "./StatusBanner";

const LABELS: Record<BadgeLabel, string> = {
  verified: strings.verifyLabelVerified,
  partial: strings.verifyLabelPartial,
  "not verified": strings.verifyLabelNotVerified,
};

/**
 * The one verify badge line: the label word, the support percent, and
 * the honest degraded note when the thresholds were missing. The answer
 * card and the verify tab share it, so the badge never reads two ways.
 */
export default function BadgeLine({
  verify,
}: {
  verify: VerifySummary | null | undefined;
}) {
  const badge = verifyBadge(verify, BADGE_THRESHOLDS);
  const pct = Math.round(badge.groundedness * 100);
  return (
    <>
      <p>
        <Tag>{LABELS[badge.label]}</Tag>
        <span>{strings.claimSupportLabel.replace("{pct}", String(pct))}</span>
      </p>
      {badge.degraded && <StatusBanner kind="warn">{strings.badgeDegradeNote}</StatusBanner>}
    </>
  );
}
