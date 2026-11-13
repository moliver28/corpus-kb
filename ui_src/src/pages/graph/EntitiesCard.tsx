import { Button, Card, Input, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { windowPagerTotal } from "../../hooks/useServerWindow";
import type { ServerWindow } from "../../hooks/useServerWindow";
import { strings } from "../../strings/graph";
import type { EntityRow } from "./shapes";
import EntityDrawer from "./EntityDrawer";

/**
 * The graph entities table (Phase-2 Wave 4): one window of the
 * server's pages (the window state lives on the page; this card is its
 * view), a type filter that rides every window read, and the
 * per-entity drawer. IDs carry the tabular figures the tokens pin.
 */
export default function EntitiesCard({ win }: { win: ServerWindow<EntityRow> }) {
  const [typeDraft, setTypeDraft] = useState("");
  const [detail, setDetail] = useState<EntityRow | null>(null);

  const columns: ColumnsType<EntityRow> = [
    {
      title: strings.graphColumnName,
      dataIndex: "name",
      key: "name",
    },
    {
      title: strings.graphColumnType,
      dataIndex: "entity_type",
      key: "entity_type",
    },
    {
      title: strings.graphColumnId,
      dataIndex: "entity_id",
      key: "entity_id",
      render: (value: string) => <span className="ui-num">{value}</span>,
    },
    {
      title: strings.graphColumnActions,
      key: "actions",
      render: (_value: unknown, row: EntityRow) => (
        <Button size="small" onClick={() => setDetail(row)}>
          {strings.graphDetails}
        </Button>
      ),
    },
  ];

  const total = windowPagerTotal(win.page, win.windowSize, win.rows.length, win.hasMore);

  // The filter applies on Enter or blur, so typing never spams the
  // server with one read per keystroke.
  function applyFilter(next: string): void {
    const trimmed = next.trim();
    setTypeDraft(next);
    if (trimmed !== (win.extraQuery?.entity_type ?? "")) {
      win.setFilter(trimmed === "" ? undefined : { entity_type: trimmed });
    }
  }

  return (
    <Card title={strings.graphEntitiesTitle}>
      <div className="ui-stack">
        <div className="ui-field">
          <label className="ui-field-label" htmlFor="graph-type-filter">
            {strings.graphFilterLabel}
          </label>
          <Input
            id="graph-type-filter"
            className="ui-narrow--short"
            allowClear
            value={typeDraft}
            onChange={(event) => setTypeDraft(event.target.value)}
            onPressEnter={() => applyFilter(typeDraft)}
            onBlur={() => applyFilter(typeDraft)}
            placeholder={strings.graphFilterLabel}
          />
        </div>
        <OpBody result={win.result} ok={win.okEnvelope} retry={win.refresh} opId="list-entities">
          {win.rows.length === 0 ? (
            <StatusBanner kind="info">{strings.graphEntitiesEmpty}</StatusBanner>
          ) : (
            <Table<EntityRow>
              size="small"
              rowKey="entity_id"
              dataSource={win.rows}
              columns={columns}
              pagination={{
                current: win.page,
                pageSize: win.windowSize,
                total,
                showSizeChanger: false,
                onChange: win.goto,
              }}
            />
          )}
        </OpBody>
      </div>
      <EntityDrawer row={detail} onClose={() => setDetail(null)} />
    </Card>
  );
}
