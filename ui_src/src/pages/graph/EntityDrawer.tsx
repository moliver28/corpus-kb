import { Button, Descriptions, Drawer, Input, Typography } from "antd";
import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import ConfirmGate from "../../components/ConfirmGate";
import LabeledField from "../../components/LabeledField";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useFormWrite } from "../../hooks/useFormWrite";
import { useOp } from "../../hooks/useOp";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings/graph";
import { isNeighborsEnvelope, isRelationsEnvelope } from "./shapes";
import type { EntityRow } from "./shapes";
import RelationRows from "./RelationRows";

function metadataWords(row: EntityRow) {
  const entries = Object.entries(row.metadata);
  if (entries.length === 0) {
    return <Typography.Text type="secondary">{strings.graphDrawerEmptyMeta}</Typography.Text>;
  }
  return (
    <Descriptions size="small" column={1}>
      {entries.map(([key, value]) => (
        <Descriptions.Item key={key} label={key}>
          <span className="ui-num">{String(value)}</span>
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

/**
 * The edit form (Phase-2 Wave 8): name, type, and the metadata
 * key/value pairs (the Wave-2 drawer pattern). One save writes the
 * whole row through the update-entity op — metadata replaces wholesale,
 * so the form edits every pair it shows.
 */
function EntityEditForm({
  row,
  refresh,
  onExit,
}: {
  row: EntityRow;
  /** The drawer's post-save refresh: reads re-run, the page re-reads. */
  refresh: () => void;
  /** Leaves edit mode (the Cancel button; the form stays on a save). */
  onExit: () => void;
}) {
  const [name, setName] = useState(row.name);
  const [entityType, setEntityType] = useState(row.entity_type);
  const [pairs, setPairs] = useState<[string, string][]>(
    Object.entries(row.metadata).map(([key, value]) => [key, String(value)]),
  );
  const [pairKey, setPairKey] = useState("");
  const [pairValue, setPairValue] = useState("");
  const { running, error, saved, rootRef, submit } = useFormWrite(
    "update-entity",
    refresh,
    strings.graphEditSaved,
  );

  function addPair(): void {
    const key = pairKey.trim();
    if (key === "") {
      return;
    }
    setPairs((current) => [...current.filter(([oldKey]) => oldKey !== key), [key, pairValue]]);
    setPairKey("");
    setPairValue("");
  }

  async function onSave(): Promise<void> {
    const metadata: Record<string, unknown> = {};
    for (const [key, value] of pairs) {
      metadata[key] = value;
    }
    await submit(
      { name: name.trim(), entity_type: entityType.trim(), metadata },
      { pathParams: { entity_id: row.entity_id } },
    );
  }

  return (
    <div className="ui-stack" data-testid="entity-edit-form">
      <div ref={rootRef} className="ui-stack">
        <LabeledField
          label={strings.graphEntityNameLabel}
          value={name}
          onChange={setName}
          required
          onPressEnter={() => void onSave()}
        />
        <LabeledField
          label={strings.graphEntityTypeLabel}
          value={entityType}
          onChange={setEntityType}
          required
          machine
          onPressEnter={() => void onSave()}
        />
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerMetadataTitle}
        </Typography.Title>
        {pairs.map(([key, value]) => (
          <div key={key} className="ui-actions">
            <span className="ui-num">{key}</span>
            <Typography.Text>{value}</Typography.Text>
            <Button
              size="small"
              type="text"
              danger
              aria-label={key}
              onClick={() => setPairs((current) => current.filter(([oldKey]) => oldKey !== key))}
            >
              ×
            </Button>
          </div>
        ))}
        <div className="ui-actions">
          <Input
            size="small"
            className="ui-narrow--short"
            aria-label={strings.graphDrawerMetadataTitle}
            placeholder="key"
            value={pairKey}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setPairKey(event.target.value)}
          />
          <Input
            size="small"
            className="ui-narrow--short"
            aria-label={`${strings.graphDrawerMetadataTitle} value`}
            placeholder="value"
            value={pairValue}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setPairValue(event.target.value)}
          />
          <Button size="small" onClick={addPair}>
            +
          </Button>
        </div>
      </div>
      {error !== null && <StatusBanner kind="warn">{error}</StatusBanner>}
      {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
      <div className="ui-actions">
        <Button type="primary" loading={running} onClick={() => void onSave()}>
          {strings.graphEditSave}
        </Button>
        <Button onClick={onExit}>{strings.confirmCancel}</Button>
      </div>
    </div>
  );
}

/**
 * One entity's drawer (Phase-2 Wave 4; Wave 8 adds the edit form, the
 * per-relation edit/remove, and the confirmed entity delete): the
 * entity's metadata, its relations (the get-entity-relations op, by
 * path slot), and its 1-hop neighbors. The newest entity wins — a slow
 * answer for an entity the user already left never renders.
 */
export default function EntityDrawer({
  row,
  onClose,
  onWritten,
  onEntityDeleted,
}: {
  row: EntityRow | null;
  onClose: () => void;
  /** Runs after every ok write: the page re-reads its window. */
  onWritten: () => void;
  /** Reports the server's cascade count; the card closes the drawer. */
  onEntityDeleted: (relationsRemoved: number) => void;
}) {
  const { read: readRelations, state: relationsState } = useRowRead("get-entity-relations");
  const { read: readNeighbors, state: neighborsState } = useRowRead("graph-neighbors");
  const [editing, setEditing] = useState(false);
  const { run: runEntityDelete, running: deleting } = useOp("delete-entity");
  const [deleteError, setDeleteError] = useState("");

  // One read pair per opened entity: relations by path slot, neighbors
  // by POST body. The read identities are stable, so the effect fires
  // once per row change.
  useEffect(() => {
    if (row === null) {
      return;
    }
    readRelations(row.entity_id, { pathParams: { entity_id: row.entity_id } });
    readNeighbors(row.entity_id, {
      body: { entity_id: row.entity_id, hops: 1 },
    });
  }, [row, readRelations, readNeighbors]);

  const refresh = useCallback(() => {
    if (row !== null) {
      readRelations(row.entity_id, { pathParams: { entity_id: row.entity_id } });
      readNeighbors(row.entity_id, {
        body: { entity_id: row.entity_id, hops: 1 },
      });
    }
    onWritten();
  }, [row, readRelations, readNeighbors, onWritten]);

  async function deleteEntity(): Promise<void> {
    if (row === null) {
      return;
    }
    setDeleteError("");
    const result = await runEntityDelete(undefined, undefined, { entity_id: row.entity_id });
    if (!result.ok) {
      setDeleteError(result.message !== "" ? result.message : result.status);
      return;
    }
    const data = result.data as { relations_removed?: number } | null;
    onEntityDeleted(data?.relations_removed ?? 0);
  }

  // Only a read addressed to the open entity may render.
  const relationsShown =
    relationsState !== null && row !== null && relationsState.rowId === row.entity_id
      ? relationsState.result
      : null;
  const neighborsShown =
    neighborsState !== null && row !== null && neighborsState.rowId === row.entity_id
      ? neighborsState.result
      : null;
  const relationRows =
    relationsShown !== null && isRelationsEnvelope(relationsShown.data)
      ? relationsShown.data.result
      : null;
  const neighborRows =
    neighborsShown !== null && isNeighborsEnvelope(neighborsShown.data)
      ? neighborsShown.data.result
      : null;
  // The drawer's own cascade figure: the relations actually loaded here
  // — the same rows the user sees. The server's honest count rides the
  // delete answer into the card's banner.
  const cascadeCount = relationRows === null ? 0 : relationRows.length;
  const open = row !== null;
  return (
    <Drawer
      title={open ? row.name : ""}
      open={open}
      onClose={onClose}
      destroyOnHidden
      footer={
        open ? (
          <div className="ui-actions">
            <ConfirmGate
              title={strings.graphEntityDeleteTitle}
              description={strings.graphEntityDeleteBody.replace(
                "{count}",
                String(cascadeCount),
              )}
              trigger={
                <Button danger loading={deleting}>
                  {strings.graphEntityDeleteTitle}
                </Button>
              }
              onConfirm={() => void deleteEntity()}
            />
            {deleteError !== "" && (
              <div className="ui-field-error" role="alert">
                {deleteError}
              </div>
            )}
          </div>
        ) : null
      }
    >
      <div className="ui-stack">
        {open && editing ? (
          <EntityEditForm row={row} refresh={refresh} onExit={() => setEditing(false)} />
        ) : (
          <>
            <div className="ui-actions">
              <Button size="small" onClick={() => setEditing(true)}>
                {strings.graphEditTitle}
              </Button>
            </div>
            <Typography.Title level={5} className="ui-flush">
              {strings.graphDrawerMetadataTitle}
            </Typography.Title>
            {open && metadataWords(row)}
          </>
        )}
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerRelationsTitle}
        </Typography.Title>
        <OpBody
          result={relationsShown}
          ok={relationRows !== null}
          skeletonRows={2}
          opId="get-entity-relations"
          retry={refresh}
        >
          {relationRows === null ? null : (
            <RelationRows rows={relationRows} refresh={refresh} />
          )}
        </OpBody>
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerNeighborsTitle}
        </Typography.Title>
        <OpBody
          result={neighborsShown}
          ok={neighborRows !== null}
          skeletonRows={2}
          opId="graph-neighbors"
          retry={refresh}
        >
          {neighborRows === null ? null : (
            <div className="ui-stack">
              {neighborRows.length === 0 && (
                <Typography.Text type="secondary">{strings.graphNeighborsEmpty}</Typography.Text>
              )}
              {neighborRows.map((item) => (
                <p key={item.entity_id} className="ui-flush">
                  {item.name}{" "}
                  <Typography.Text type="secondary">
                    ({item.entity_type}, {item.depth})
                  </Typography.Text>
                </p>
              ))}
            </div>
          )}
        </OpBody>
      </div>
    </Drawer>
  );
}
