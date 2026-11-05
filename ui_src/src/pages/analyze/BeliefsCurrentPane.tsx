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
  // The empty box means "every subject": the key is OMITTED so the
  // store's `$2 IS NULL OR subject = $2` branch runs. Sending `""`
  // would filter subject = '' and hide every row.
  const load = useCallback((): void => {
    reset();
    void run(subject === "" ? { limit: 50 } : { subject, limit: 50 });
  }, [run, reset, subject]);
  // The landing read runs ONCE on mount (stable deps — `run`/`reset`
  // never change). Keystrokes never POST; a typed subject goes through
  // the Show button only, whose read is `load` above.
  const landingLoad = useCallback((): void => {
    reset();
    void run({ limit: 50 });
  }, [run, reset]);
  useEffect(() => {
    landingLoad();
  }, [landingLoad]);
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
