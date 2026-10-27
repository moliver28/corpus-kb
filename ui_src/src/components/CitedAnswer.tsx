import { Card, Tag } from "antd";
import { useState } from "react";
import { verifyBadge, type VerifySummary } from "../api/verify";
import { BADGE_THRESHOLDS } from "../api/thresholds.generated";
import { strings } from "../strings";

export interface Citation {
  text: string;
  source: string;
  /** The distinct source labels this sentence cites (hover-citation link). */
  sources?: string[];
}

const LABELS: Record<"verified" | "partial" | "not verified", string> = {
  verified: strings.verifyLabelVerified,
  partial: strings.verifyLabelPartial,
  "not verified": strings.verifyLabelNotVerified,
};

/** One source label -> its basename (full paths never render on screen). */
function basename(label: string): string {
  const last = label.split(/[\\/]/).pop();
  return last === undefined || last === "" ? label : last;
}

/** One joined label list -> basenames for display, full paths for title. */
function displaySources(cited: string[]): { text: string; full: string } {
  return {
    text: cited.map(basename).join(", "),
    full: cited.join(", "),
  };
}

/**
 * A grounded answer: the text, the verify badge, and the cited sentences.
 * The hover-citation link is the signature interaction: hovering (or
 * focusing) one sentence raises it and marks every sentence that cites
 * the same source, with its source label emphasized.
 */
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
  const [hovered, setHovered] = useState<number | null>(null);
  const activeSources =
    hovered === null ? null : new Set(citations[hovered]?.sources ?? []);
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
      <ol className="ui-citations" onMouseLeave={() => setHovered(null)}>
        {citations.map((citation, index) => {
          const cited = citation.sources ?? [citation.source];
          const active = hovered === index;
          const linked =
            hovered !== null &&
            !active &&
            cited.some((label) => activeSources?.has(label) ?? false);
          const className =
            "ui-citation" +
            (active ? " ui-citation--active" : "") +
            (linked ? " ui-citation--linked" : "");
          const shown = displaySources(cited);
          return (
            <li
              key={`${index}-${citation.source}`}
              className={className}
              tabIndex={0}
              onMouseEnter={() => setHovered(index)}
              onFocus={() => setHovered(index)}
              onBlur={() => setHovered(null)}
            >
              <span className="ui-citation-text">{citation.text}</span>{" "}
              <span
                title={shown.full}
                className={
                  active ? "ui-citation-source ui-citation-source--active" : "ui-citation-source"
                }
              >
                ({shown.text})
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
