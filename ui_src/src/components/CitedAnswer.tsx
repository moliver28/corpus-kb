import { Card } from "antd";
import { useId, useState } from "react";
import type { VerifySummary } from "../api/verify";
import BadgeLine from "./BadgeLine";
import { strings } from "../strings";

export interface Citation {
  text: string;
  source: string;
  /** The distinct source labels this sentence cites (hover-citation link). */
  sources?: string[];
}

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
  const [hovered, setHovered] = useState<number | null>(null);
  // Collision-proof id prefix for the sentence -> source description.
  const idPrefix = useId();
  const activeSources =
    hovered === null ? null : new Set(citations[hovered]?.sources ?? []);
  return (
    <Card title={strings.citedAnswerTitle}>
      <p className="ui-prose">{answer}</p>
      {/* The generated policy thresholds ride in (the one numeric-policy
          source); a missing or invalid threshold still degrades the badge. */}
      <BadgeLine verify={verify} />
      <h2 className="ui-heading">{strings.citationsHeading}</h2>
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
          // The focusable sentence carries its source as the accessible
          // description: a screen reader reads the sentence plus the
          // label, not a bare list item.
          const sourceId = `${idPrefix}-source-${index}`;
          return (
            <li
              key={`${index}-${citation.source}`}
              className={className}
              tabIndex={0}
              aria-describedby={sourceId}
              onMouseEnter={() => setHovered(index)}
              onFocus={() => setHovered(index)}
              onBlur={() => setHovered(null)}
            >
              <span className="ui-citation-text">{citation.text}</span>{" "}
              <span
                id={sourceId}
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
