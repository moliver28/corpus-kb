// The Beliefs card's Timeline tab: one era-history read over
// POST /api/beliefs/timeline, driven by the subject/relation/object
// triple.
import { Table } from "antd";
import { useCallback, useState } from "react";
import { percentText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { ERA_COLUMNS } from "./beliefColumns";
import { isTimelineEnvelope, type EraRow } from "./beliefsShapes";
import TripleInputs from "./BeliefsTripleInputs";

/** One era-history read (POST /api/beliefs/timeline). */
export default function TimelinePane() {
  const [subject, setSubject] = useState("");
  const [relation, setRelation] = useState("");
  const [object, setObject] = useState("");
  const timeline = useOp("beliefs-timeline");
  const { run, reset } = timeline;
  const load = useCallback((): void => {
    reset();
    void run({ subject, relation, object });
  }, [run, reset, subject, relation, object]);
  const result = timeline.result;
  const ok = result !== null && isTimelineEnvelope(result.data);
  return (
    <div className="ui-stack">
      <TripleInputs
        subject={subject}
        relation={relation}
        object={object}
        setSubject={setSubject}
        setRelation={setRelation}
        setObject={setObject}
        onRun={load}
        runLabel={strings.analyzeBeliefsRunLabel}
      />
      <OpBody result={result} ok={ok} retry={load} opId="beliefs-timeline">
        {ok && result !== null && isTimelineEnvelope(result.data) ? (
          result.data.eras.length === 0 ? (
            <StatusBanner kind="info">{strings.analyzeBeliefsTimelineEmpty}</StatusBanner>
          ) : (
            <div className="ui-stack">
              <p className="ui-flush">
                {strings.analyzeBeliefsPosteriorLine.replace(
                  "{mean}",
                  percentText(result.data.posterior.mean),
                )}
              </p>
              <Table<EraRow>
                size="small"
                rowKey="start"
                pagination={false}
                dataSource={result.data.eras}
                columns={ERA_COLUMNS}
              />
            </div>
          )
        ) : null}
      </OpBody>
    </div>
  );
}
