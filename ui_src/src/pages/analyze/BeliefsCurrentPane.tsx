// The Beliefs card's Current tab: the landing read and the typed
// subject read over POST /api/beliefs/current.
import { Button, Input, Table } from "antd";
import { useCallback, useEffect, useState } from "react";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { CURRENT_COLUMNS } from "./beliefColumns";
import { isBeliefsEnvelope, type BeliefRow } from "./beliefsShapes";

/** One current-posteriors read (POST /api/beliefs/current). */
export default function CurrentPane() {
  const [subject, setSubject] = useState("");
  const current = useOp("beliefs-current");
  const { run, reset } = current;
  const load = useCallback((): void => {
    reset();
    void run({ subject, limit: 50 });
  }, [run, reset, subject]);
  // The landing read: the current posteriors load on mount, then follow
  // the typed subject through the Show button.
  useEffect(() => {
    load();
  }, [load]);
  const result = current.result;
  const ok = result !== null && isBeliefsEnvelope(result.data);
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
      <OpBody result={result} ok={ok} retry={load} opId="beliefs-current">
        {ok && result !== null && isBeliefsEnvelope(result.data) ? (
          result.data.beliefs.length === 0 ? (
            <StatusBanner kind="info">{strings.analyzeBeliefsEmpty}</StatusBanner>
          ) : (
            <Table<BeliefRow>
              size="small"
              rowKey="belief_id"
              pagination={false}
              dataSource={result.data.beliefs}
              columns={CURRENT_COLUMNS}
            />
          )
        ) : null}
      </OpBody>
    </div>
  );
}
