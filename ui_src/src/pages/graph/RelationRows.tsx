import { Button, Popconfirm, Typography } from "antd";
import { useState } from "react";
import LabeledField from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import { useFormWrite } from "../../hooks/useFormWrite";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/graph";
import type { RelationRow } from "./shapes";

/**
 * The drawer's relation rows (Phase-2 Wave 8): each row edits its type
 * and weight inline (the update-relation op, by path slot) and removes
 * itself behind a Popconfirm (the delete-relation op — single-record
 * destructive). Every ok answer re-runs the caller's refresh, so the
 * drawer's lists and the page's window agree with the server.
 */

function RelationRowItem({
  row,
  refresh,
  onRemoved,
}: {
  row: RelationRow;
  refresh: () => void;
  /** Reports an ok removal; the parent owns the surviving banner. */
  onRemoved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [relationType, setRelationType] = useState(row.relation_type);
  const [weightDraft, setWeightDraft] = useState("");
  const {
    running,
    error,
    saved,
    rootRef,
    submit,
  } = useFormWrite("update-relation", refresh, strings.graphRelationUpdated);
  const { run: runDelete, running: deleting } = useOp("delete-relation");
  const [deleteError, setDeleteError] = useState("");

  async function onSave(): Promise<void> {
    const body: Record<string, unknown> = { relation_type: relationType.trim() };
    if (weightDraft.trim() !== "") {
      body.weight = Number(weightDraft.trim());
    }
    const ok = await submit(body, { pathParams: { relation_id: row.relation_id } });
    if (ok) {
      setEditing(false);
      setWeightDraft("");
    }
  }

  async function onRemove(): Promise<void> {
    setDeleteError("");
    const result = await runDelete(undefined, undefined, { relation_id: row.relation_id });
    if (result.ok) {
      onRemoved();
      refresh();
      return;
    }
    setDeleteError(result.message !== "" ? result.message : result.status);
  }

  if (editing) {
    return (
      <div className="ui-stack" data-testid={`relation-edit-${row.relation_id}`}>
        <div ref={rootRef} className="ui-stack">
          <LabeledField
            label={strings.graphRelationTypeLabel}
            value={relationType}
            onChange={setRelationType}
            machine
            onPressEnter={() => void onSave()}
          />
          <LabeledField
            label={strings.graphWeightLabel}
            value={weightDraft}
            onChange={setWeightDraft}
            machine
            onPressEnter={() => void onSave()}
          />
        </div>
        {error !== null && <StatusBanner kind="warn">{error}</StatusBanner>}
        <div className="ui-actions">
          <Button size="small" type="primary" loading={running} onClick={() => void onSave()}>
            {strings.graphEditSave}
          </Button>
          <Button size="small" onClick={() => setEditing(false)}>
            {strings.confirmCancel}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="ui-stack">
      <div className="ui-actions">
        <span className="ui-flush">
          {strings.graphRelationRow
            .replace("{source}", row.source_name)
            .replace("{relation}", row.relation_type)
            .replace("{target}", row.target_name)}
        </span>
        <span className="ui-num">{row.weight ?? ""}</span>
        <Button size="small" onClick={() => setEditing(true)}>
          {strings.graphRelationEditTitle}
        </Button>
        <Popconfirm
          title={strings.graphRelationDeleteTitle}
          description={strings.graphRelationDeleteBody}
          okText={strings.graphRelationDeleteTitle}
          cancelText={strings.confirmCancel}
          onConfirm={() => void onRemove()}
        >
          <Button size="small" danger loading={deleting}>
            {strings.graphRelationDeleteTitle}
          </Button>
        </Popconfirm>
      </div>
      {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
      {deleteError !== "" && (
        <div className="ui-field-error" role="alert">
          {deleteError}
        </div>
      )}
      <Typography.Text type="secondary" className="ui-num">
        {row.relation_id}
      </Typography.Text>
    </div>
  );
}

export default function RelationRows({
  rows,
  refresh,
}: {
  rows: RelationRow[];
  refresh: () => void;
}) {
  // The removal banner lives here: the removed row unmounts when the
  // list re-reads, and the ok word must survive it.
  const [removedMsg, setRemovedMsg] = useState("");
  return (
    <div className="ui-stack">
      {removedMsg !== "" && <StatusBanner kind="ok">{removedMsg}</StatusBanner>}
      {rows.length === 0 && (
        <Typography.Text type="secondary">{strings.graphRelationsEmpty}</Typography.Text>
      )}
      {rows.map((row) => (
        <RelationRowItem
          key={row.relation_id}
          row={row}
          refresh={refresh}
          onRemoved={() => setRemovedMsg(strings.graphRelationDeleted)}
        />
      ))}
    </div>
  );
}
