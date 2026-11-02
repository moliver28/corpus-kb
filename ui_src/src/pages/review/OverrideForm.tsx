import { Button, Input } from "antd";
import { useState } from "react";
import { strings } from "../../strings";

/**
 * The override form, inline under the queue (no modal): the note stays
 * next to the rows it judges. The empty-note guard lives with the
 * caller, so the warning banner keeps its place above the queue body.
 */
export default function OverrideForm({
  onSubmit,
  onCancel,
}: {
  /** One trimmed note ("" when the reviewer sent nothing). */
  onSubmit: (note: string) => void;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="ui-stack">
      <p>
        <strong>{strings.reviewOverrideTitle}</strong>
      </p>
      <p>{strings.reviewOverrideBody}</p>
      <Input.TextArea
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder={strings.reviewNoteLabel}
        rows={3}
      />
      <div className="ui-actions">
        <Button type="primary" onClick={() => onSubmit(note.trim())}>
          {strings.reviewOverrideSubmit}
        </Button>
        <Button onClick={onCancel}>{strings.confirmCancel}</Button>
      </div>
    </div>
  );
}
