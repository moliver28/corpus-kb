// The Beliefs card's Compare tab: one side-by-side read over
// POST /api/beliefs/compare for one typed subject.
import { Button, Input, Table } from "antd";
import { useCallback, useState } from "react";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/analyze";
import { CURRENT_COLUMNS } from "./beliefColumns";
import { isCompareEnvelope, type BeliefRow } from "./beliefsShapes";

/** One side-by-side read (POST /api/beliefs/compare). */
export default function ComparePane() {
  const [subject, setSubject] = useState("");
  const compare = useOp("beliefs-compare");
  const { run, reset } = compare;
  const load = useCallback((): void => {
    reset();
    void run({ subject });
  }, [run, reset, subject]);
  const result = compare.result;
  const ok = result !== null && isCompareEnvelope(result.data);
  return (
    <div className="ui-stack">
      <div className="ui-actions">
        <Input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder={strings.analyzeBeliefsSubjectLabel}
          aria-label={strings.analyzeBeliefsSubjectLabel}
        />
        <Button type="primary" onClick={load}>
          {strings.analyzeBeliefsRunLabel}
        </Button>
      </div>
      <OpBody result={result} ok={ok} retry={load} opId="beliefs-compare">
        {ok && result !== null && isCompareEnvelope(result.data) ? (
          result.data.rows.length === 0 ? (
            <StatusBanner kind="info">{strings.analyzeBeliefsCompareEmpty}</StatusBanner>
          ) : (
            <Table<BeliefRow>
              size="small"
              rowKey="belief_id"
              pagination={false}
              dataSource={result.data.rows}
              columns={CURRENT_COLUMNS}
            />
          )
        ) : null}
      </OpBody>
    </div>
  );
}
