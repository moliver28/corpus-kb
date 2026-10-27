import { Card, Tag } from "antd";
import { verifyBadge, type VerifySummary } from "../api/verify";
import { BADGE_THRESHOLDS } from "../api/thresholds.generated";
import { strings } from "../strings";

export interface Citation {
  text: string;
  source: string;
}

const LABELS: Record<"verified" | "partial" | "not verified", string> = {
  verified: strings.verifyLabelVerified,
  partial: strings.verifyLabelPartial,
  "not verified": strings.verifyLabelNotVerified,
};

/** A grounded answer: the text, the verify badge, and the cited sentences. */
export default function CitedAnswer({
  answer,
  citations,
  verify,
}: {
  answer: string;
  citations: Citation[];
  verify: VerifySummary | null;
}) {
  // The generated policy thresholds ride in (the one numeric-policy
  // source); a missing or invalid threshold still degrades the badge.
  const badge = verifyBadge(verify, BADGE_THRESHOLDS);
  const pct = Math.round(badge.groundedness * 100);
  return (
    <Card title={strings.citedAnswerTitle}>
      <p className="ui-answer">{answer}</p>
      <p>
        <Tag>{LABELS[badge.label]}</Tag>
        <span>{strings.claimSupportLabel.replace("{pct}", String(pct))}</span>
      </p>
      {badge.degraded && (
        <p className="ui-banner ui-banner--warn">{strings.badgeDegradeNote}</p>
      )}
      <h4>{strings.citationsHeading}</h4>
      <ol className="ui-citations">
        {citations.map((citation, index) => (
          <li key={`${index}-${citation.source}`}>
            <span className="ui-citation-text">{citation.text}</span>{" "}
            <span className="ui-citation-source">({citation.source})</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
