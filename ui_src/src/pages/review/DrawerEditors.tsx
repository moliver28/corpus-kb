import { Button, Popconfirm, Tag } from "antd";
import { useState } from "react";
import LabeledField from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import { useFormWrite } from "../../hooks/useFormWrite";
import { strings } from "../../strings";
import { tagColor } from "./shapes";
import type { TagRow } from "./shapes";

/**
 * The drawer's write forms (Phase-2 Wave 2): the add-tag form, the
 * per-tag remove behind a Popconfirm, and the metadata key/value
 * editor. Every write runs through the form-to-write seam hook
 * (useFormWrite) over the tag/metadata manifest ops, and every ok
 * answer re-reads the drawer's rows through the caller's refresh.
 */

export function TagEditor({
  docId,
  rows,
  refresh,
}: {
  docId: string;
  rows: TagRow[];
  refresh: () => void;
}) {
  const [name, setName] = useState("");
  const {
    running: addRunning,
    error: addError,
    saved: addSaved,
    rootRef: addRootRef,
    submit: submitAddOp,
  } = useFormWrite("tag-document", refresh, strings.reviewTagAdded);
  const {
    error: removeError,
    saved: removeSaved,
    submit: submitUntagOp,
  } = useFormWrite("untag-document", refresh, strings.reviewTagRemoved);

  function submitAdd(): void {
    const trimmed = name.trim();
    if (trimmed === "") {
      return;
    }
    void submitAddOp({ tag: trimmed }, { pathParams: { doc_id: docId } }).then((ok) => {
      if (ok) {
        setName("");
      }
    });
  }

  function removeTag(tag: string): void {
    void submitUntagOp({ tag }, { pathParams: { doc_id: docId } });
  }

  return (
    <div className="ui-stack">
      <div className="ui-actions">
        {rows.map((row) => (
          <Tag key={row.name} color={tagColor(row.color)}>
            {row.name}
            <Popconfirm
              title={strings.reviewTagRemoveTitle.replace("{tag}", row.name)}
              okText={strings.reviewTagRemoveConfirm}
              cancelText={strings.confirmCancel}
              onConfirm={() => removeTag(row.name)}
            >
              <Button
                size="small"
                type="text"
                danger
                aria-label={strings.reviewTagRemoveButton.replace("{tag}", row.name)}
              >
                ×
              </Button>
            </Popconfirm>
          </Tag>
        ))}
      </div>
      {addSaved !== null && <StatusBanner kind="ok">{addSaved}</StatusBanner>}
      {removeSaved !== null && <StatusBanner kind="ok">{removeSaved}</StatusBanner>}
      {removeError !== null && (
        <div className="ui-field-error" role="alert">
          {removeError}
        </div>
      )}
      <div ref={addRootRef}>
        <LabeledField
          label={strings.reviewTagAddLabel}
          value={name}
          onChange={setName}
          error={addError ?? undefined}
          onPressEnter={submitAdd}
        />
      </div>
      <div className="ui-actions">
        <Button
          type="primary"
          size="small"
          disabled={name.trim() === ""}
          loading={addRunning}
          onClick={submitAdd}
        >
          {strings.reviewTagAddButton}
        </Button>
      </div>
    </div>
  );
}

export function MetadataEditor({ docId, refresh }: { docId: string; refresh: () => void }) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const {
    running,
    error,
    saved,
    rootRef,
    submit: submitOp,
  } = useFormWrite("set-metadata", refresh, strings.reviewMetadataSaved);

  function submit(): void {
    if (key.trim() === "") {
      return;
    }
    void submitOp({ key: key.trim(), value, doc_id: docId }).then((ok) => {
      if (ok) {
        setKey("");
        setValue("");
      }
    });
  }

  return (
    <div className="ui-stack">
      {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
      <div ref={rootRef}>
        <LabeledField
          label={strings.reviewMetadataKeyLabel}
          value={key}
          onChange={setKey}
          error={error ?? undefined}
          machine
          widthClass="ui-narrow"
        />
        <LabeledField
          label={strings.reviewMetadataValueLabel}
          value={value}
          onChange={setValue}
          machine
          widthClass="ui-narrow"
          onPressEnter={submit}
        />
      </div>
      <div className="ui-actions">
        <Button
          type="primary"
          size="small"
          disabled={key.trim() === ""}
          loading={running}
          onClick={submit}
        >
          {strings.reviewMetadataSaveButton}
        </Button>
      </div>
    </div>
  );
}
