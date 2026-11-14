import { List, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { getMethod, type MethodGuidance } from "../../api/methods.generated";
import { progression } from "../../strings/progression";
import { rungLabel } from "./rungs";

function conceptLabel(concept: string): string {
  return concept.split("_").join(" ");
}

/** Link labels: the target method's name and id, or the bare id when unknown. */
function linkLabels(ids: readonly string[], namesById: ReadonlyMap<string, string>): string {
  return ids
    .map((id) => {
      const name = namesById.get(id) ?? getMethod(id)?.name;
      return name === undefined ? id : `${name} (${id})`;
    })
    .join("; ");
}

/** One method row: name, rung, human-led tag, summary, and guidance lines. */
function MethodRow({
  method,
  namesById,
}: {
  method: MethodGuidance;
  namesById: ReadonlyMap<string, string>;
}): ReactNode {
  const header = (
    <div>
      <Typography.Text strong>{method.name}</Typography.Text> <Tag>{method.id}</Tag>{" "}
      <Tag>{rungLabel(method.difficulty)}</Tag>
      {method.op === null && <Tag>{progression.progressionHumanOnly}</Tag>}
    </div>
  );
  return (
    <div className="ui-stack">
      {header}
      <Typography.Text type="secondary">{method.summary}</Typography.Text>
      {method.concepts.length > 0 && (
        <Typography.Text>
          {progression.progressionTeachesLabel}: {method.concepts.map(conceptLabel).join(", ")}
        </Typography.Text>
      )}
      {method.prerequisites.length > 0 && (
        <Typography.Text>
          {progression.progressionNeedsLabel}: {linkLabels(method.prerequisites, namesById)}
        </Typography.Text>
      )}
      {method.next.length > 0 && (
        <Typography.Text>
          {progression.progressionNextLabel}: {linkLabels(method.next, namesById)}
        </Typography.Text>
      )}
    </div>
  );
}

/**
 * One rung's methods, in the generated table's id order. Every name,
 * concept, and link comes from the generated register table — this
 * component hard-codes none of them. Link names resolve inside the
 * rendered table first, then the generated register table.
 */
export default function ProgressionMethodList({
  methods,
  namesById,
}: {
  methods: readonly MethodGuidance[];
  namesById: ReadonlyMap<string, string>;
}) {
  return (
    <List
      size="small"
      rowKey="id"
      dataSource={[...methods]}
      renderItem={(method) => (
        <List.Item className="ui-flush">
          <MethodRow method={method} namesById={namesById} />
        </List.Item>
      )}
    />
  );
}
