import { Card } from "antd";
import { getOp } from "../api/ops";
import StatusBanner from "./StatusBanner";

/** One honest not-wired line, rendered only while the op row is absent. */
function Line({ opId, text }: { opId: string; text: string }) {
  if (getOp(opId) !== undefined) {
    return null;
  }
  return <StatusBanner kind="warn">{text}</StatusBanner>;
}

/** One not-wired row: the manifest op id to check and the line to show. */
interface NotWiredItem {
  opId: string;
  text: string;
}

/**
 * A card of steps whose manifest rows have not landed. Each line
 * upgrades itself (and disappears) the moment its op joins the manifest.
 */
export default function NotWiredCard({ title, items }: { title: string; items: NotWiredItem[] }) {
  return (
    <Card title={title}>
      <div className="ui-stack">
        {items.map((item) => (
          <Line key={item.opId} opId={item.opId} text={item.text} />
        ))}
      </div>
    </Card>
  );
}
