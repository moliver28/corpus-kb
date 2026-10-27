import { strings } from "../../strings";

export interface EvidenceItem {
  text: string;
  source: string;
}

/** The ranked evidence list under an answer (or in place of one). */
export default function EvidenceList({ items }: { items: EvidenceItem[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <>
      <h4>{strings.askEvidenceHeading}</h4>
      <ol className="ui-citations">
        {items.map((item, index) => (
          <li key={`${index}-${item.source}`}>
            <span className="ui-citation-text">{item.text}</span>{" "}
            <span className="ui-citation-source">({item.source})</span>
          </li>
        ))}
      </ol>
    </>
  );
}
