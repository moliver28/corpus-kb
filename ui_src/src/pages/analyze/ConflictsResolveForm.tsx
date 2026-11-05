import { Card, Radio } from "antd";
import { useState } from "react";
import ConfirmGate from "../../components/ConfirmGate";
import LabeledField from "../../components/LabeledField";
import { useFormWrite } from "../../hooks/useFormWrite";
import { strings } from "../../strings";
import type { ConflictRow } from "./derivedShapes";

const VERDICTS = ["conflict", "revision", "dismissed"] as const;

type Verdict = (typeof VERDICTS)[number];

const VERDICT_LABELS: Record<Verdict, string> = {
  conflict: strings.analyzeConflictsVerdictConflict,
  revision: strings.analyzeConflictsVerdictRevision,
  dismissed: strings.analyzeConflictsVerdictDismissed,
};

/**
 * One resolve editor: the human finalize path for one conflict flag.
 * The write sits behind the confirm gate, and the gate NAMES the pair
 * and the recorded reviewer — the act never runs from a stray press
 * (the gate stays disabled until the reviewer name is typed). A typed
 * failure lands inline under the form and the editor stays open; an ok
 * write closes the editor, and the refreshed row shows the persisted
 * verdict and reviewer name — the record IS the audit trail.
 */
export default function ConflictsResolveForm({
  conflict,
  refresh,
  onDone,
}: {
  conflict: ConflictRow;
  refresh: () => void;
  onDone: () => void;
}) {
  const [verdict, setVerdict] = useState<Verdict>("revision");
  const [reviewer, setReviewer] = useState("");
  // The ok write closes this editor: the refreshed row shows the
  // persisted verdict and reviewer name. The seam's saved word states
  // the same outcome (the settle wording with the chosen verdict).
  const { error, rootRef, submit } = useFormWrite(
    "conflicts-resolve",
    strings.analyzeConflictsResolved.replace("{verdict}", verdict),
    () => {
      refresh();
      onDone();
    },
  );
  const body = strings.analyzeConflictsResolveBody
    .replace("{a}", conflict.statement_a_id)
    .replace("{b}", conflict.statement_b_id)
    .replace("{verdict}", VERDICT_LABELS[verdict])
    .replace("{reviewer}", reviewer.trim());

  return (
    <Card size="small" title={strings.analyzeConflictsResolveCard}>
      <div ref={rootRef} className="ui-stack">
        <p className="ui-flush">
          <span className="ui-row-id">{conflict.statement_a_id}</span>{" "}
          {strings.analyzeConflictsPairWord}{" "}
          <span className="ui-row-id">{conflict.statement_b_id}</span>
        </p>
        <Radio.Group
          aria-label={strings.analyzeConflictsVerdictLabel}
          value={verdict}
          onChange={(e) => setVerdict(e.target.value as Verdict)}
          options={VERDICTS.map((v) => ({ value: v, label: VERDICT_LABELS[v] }))}
        />
        <LabeledField
          label={strings.analyzeConflictsReviewerLabel}
          value={reviewer}
          onChange={setReviewer}
          required
        />
        {error !== null && (
          <p role="alert" className="ui-flush">
            {error}
          </p>
        )}
        <ConfirmGate
          title={strings.analyzeConflictsResolveTitle.replace("{id}", conflict.conflict_id)}
          description={body}
          confirmLabel={strings.analyzeConflictsResolveButton}
          disabled={reviewer.trim() === ""}
          onConfirm={() => {
            void submit({
              conflict_id: conflict.conflict_id,
              resolved_verdict: verdict,
              resolved_by: reviewer.trim(),
            });
          }}
        />
      </div>
    </Card>
  );
}
