import { strings } from "../strings/app";

/** Collapsible pretty-printed JSON (the raw envelope, never the headline). */
export default function JsonViewer({ value }: { value: unknown }) {
  return (
    <details className="ui-json">
      <summary>{strings.receiptRaw}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
