// One subject/relation/object input group shared by the Beliefs card's
// timeline and predict panes: three labeled boxes and the run button.
import { Button, Input } from "antd";
import { strings } from "../../strings";

/** One subject/relation/object triple input group. */
export default function TripleInputs({
  subject,
  relation,
  object,
  setSubject,
  setRelation,
  setObject,
  onRun,
  runLabel,
}: {
  subject: string;
  relation: string;
  object: string;
  setSubject: (v: string) => void;
  setRelation: (v: string) => void;
  setObject: (v: string) => void;
  onRun: () => void;
  runLabel: string;
}) {
  return (
    <div className="ui-actions">
      <Input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder={strings.analyzeBeliefsSubjectLabel}
        aria-label={strings.analyzeBeliefsSubjectLabel}
      />
      <Input
        value={relation}
        onChange={(e) => setRelation(e.target.value)}
        placeholder={strings.analyzeBeliefsRelationLabel}
        aria-label={strings.analyzeBeliefsRelationLabel}
      />
      <Input
        value={object}
        onChange={(e) => setObject(e.target.value)}
        placeholder={strings.analyzeBeliefsObjectLabel}
        aria-label={strings.analyzeBeliefsObjectLabel}
      />
      <Button type="primary" onClick={onRun}>
        {runLabel}
      </Button>
    </div>
  );
}
