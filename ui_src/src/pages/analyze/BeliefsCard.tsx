import { Button, Card, Input, Table, Tabs, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { percentText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import {
  isBeliefsEnvelope,
  isCompareEnvelope,
  isPredictEnvelope,
  isTimelineEnvelope,
  type BeliefRow,
  type EraRow,
} from "./derivedShapes";

const CURRENT_COLUMNS: ColumnsType<BeliefRow> = [
  { title: strings.analyzeBeliefsColumnSubject, dataIndex: "subject", key: "subject" },
  { title: strings.analyzeBeliefsColumnRelation, dataIndex: "relation", key: "relation" },
  { title: strings.analyzeBeliefsColumnObject, dataIndex: "object", key: "object" },
  {
    title: strings.analyzeBeliefsColumnMean,
    key: "mean",
    align: "right",
    render: (_v, row) => (
      <span className="ui-num">{percentText(row.alpha / (row.alpha + row.beta))}</span>
    ),
  },
  {
    title: strings.analyzeBeliefsColumnEvidence,
    dataIndex: "evidence_count",
    key: "evidence_count",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
];

const ERA_COLUMNS: ColumnsType<EraRow> = [
  {
    title: strings.analyzeBeliefsColumnEra,
    key: "era",
    render: (_v, row) => (
      <span className="ui-num">
        {row.start.slice(0, 10)} - {row.end.slice(0, 10)}
      </span>
    ),
  },
  {
    title: strings.analyzeBeliefsColumnPositives,
    dataIndex: "positives",
    key: "positives",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
  {
    title: strings.analyzeBeliefsColumnNegatives,
    dataIndex: "negatives",
    key: "negatives",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
  {
    title: strings.analyzeBeliefsColumnMean,
    key: "mean",
    align: "right",
    render: (_v, row) => <span className="ui-num">{percentText(row.mean)}</span>,
  },
];

/** One subject/relation/object triple input group. */
function TripleInputs({
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

/** One current-posteriors read (POST /api/beliefs/current). */
function CurrentPane() {
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

/** One era-history read (POST /api/beliefs/timeline). */
function TimelinePane() {
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

/** One posterior-predictive read (POST /api/beliefs/predict). */
function PredictPane() {
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

/** One side-by-side read (POST /api/beliefs/compare). */
function ComparePane() {
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

/**
 * The beliefs family in one card: current posteriors, era history,
 * posterior prediction, and the side-by-side comparison. One family,
 * one card (LabelingCard pattern).
 */
export default function BeliefsCard() {
  return (
    <Card title={strings.analyzeBeliefsCardTitle}>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.analyzeBeliefsCardBody}
      </Typography.Paragraph>
      <Tabs
        items={[
          { key: "current", label: strings.analyzeBeliefsTabCurrent, children: <CurrentPane /> },
          { key: "timeline", label: strings.analyzeBeliefsTabTimeline, children: <TimelinePane /> },
          { key: "predict", label: strings.analyzeBeliefsTabPredict, children: <PredictPane /> },
          { key: "compare", label: strings.analyzeBeliefsTabCompare, children: <ComparePane /> },
        ]}
      />
    </Card>
  );
}
