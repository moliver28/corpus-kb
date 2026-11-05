// The Beliefs card's Predict tab: one posterior-predictive read over
// POST /api/beliefs/predict, driven by the subject/relation/object
// triple.
import { useCallback, useState } from "react";
import { percentText } from "../../api/format";
import OpBody from "../../components/OpBody";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isPredictEnvelope } from "./beliefsShapes";
import TripleInputs from "./BeliefsTripleInputs";

/** One posterior-predictive read (POST /api/beliefs/predict). */
export default function PredictPane() {
  const [subject, setSubject] = useState("");
  const [relation, setRelation] = useState("");
  const [object, setObject] = useState("");
  const predict = useOp("beliefs-predict");
  const { run, reset } = predict;
  const load = useCallback((): void => {
    reset();
    void run({ subject, relation, object });
  }, [run, reset, subject, relation, object]);
  const result = predict.result;
  const ok = result !== null && isPredictEnvelope(result.data);
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
      <OpBody result={result} ok={ok} retry={load} opId="beliefs-predict">
        {ok && result !== null && isPredictEnvelope(result.data) ? (
          <div className="ui-stack">
            <p className="ui-flush">
              {strings.analyzeBeliefsPredictLine.replace(
                "{prob}",
                percentText(result.data.probability),
              )}
            </p>
            <p className="ui-flush">
              {strings.analyzeBeliefsIntervalWord
                .replace("{low}", percentText(result.data.interval_low))
                .replace("{high}", percentText(result.data.interval_high))}
            </p>
          </div>
        ) : null}
      </OpBody>
    </div>
  );
}
