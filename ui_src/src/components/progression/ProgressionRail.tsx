import { Card, Collapse, Typography } from "antd";
import type { CollapseProps } from "antd";
import { METHODS, type MethodGuidance } from "../../api/methods.generated";
import { progression } from "../../strings/progression";
import ProgressionMethodList from "./ProgressionMethodList";
import { RUNGS, rungLabel } from "./rungs";

interface RungGroup {
  key: string;
  label: string;
  methods: MethodGuidance[];
}

/**
 * Group the register's methods by difficulty rung, in fixed rung order,
 * with the id order kept inside each rung. The untagged bucket renders
 * last and only when it holds methods.
 */
function groupByRung(methods: readonly MethodGuidance[]): RungGroup[] {
  const groups: RungGroup[] = RUNGS.map((rung) => ({ ...rung, methods: [] }));
  const untagged: RungGroup = { key: "untagged", label: rungLabel(null), methods: [] };
  for (const method of methods) {
    const group = groups.find((rung) => rung.key === method.difficulty) ?? untagged;
    group.methods.push(method);
  }
  return untagged.methods.length === 0 ? groups : [...groups, untagged];
}

/**
 * The progression rail: the analysis register's difficulty ladder as
 * data. One collapsed panel per rung (progressive disclosure), each
 * method with what it teaches, what it needs first, and what builds on
 * it. All content comes from the generated methods table — no method
 * name, rung, or link is hard-coded here.
 */
/** "{count} method(s)" with honest singular/plural from the string table. */
function countLabel(count: number): string {
  const template = count === 1 ? progression.progressionMethodCountOne : progression.progressionMethodCount;
  return template.replace("{count}", String(count));
}

export default function ProgressionRail({
  methods = METHODS,
}: {
  methods?: readonly MethodGuidance[];
}) {
  const groups = groupByRung(methods);
  const namesById = new Map(methods.map((method) => [method.id, method.name]));
  const items: CollapseProps["items"] = groups.map((group) => ({
    key: group.key,
    label: (
      <span>
        {group.label}{" "}
        <Typography.Text type="secondary">{countLabel(group.methods.length)}</Typography.Text>
      </span>
    ),
    children:
      group.methods.length === 0 ? (
        <Typography.Text type="secondary">{progression.progressionRungEmpty}</Typography.Text>
      ) : (
        <ProgressionMethodList methods={group.methods} namesById={namesById} />
      ),
  }));
  return (
    <Card title={progression.progressionTitle} className="ui-stack">
      <Typography.Paragraph type="secondary">{progression.progressionBody}</Typography.Paragraph>
      <Collapse items={items} defaultActiveKey={[]} />
    </Card>
  );
}
