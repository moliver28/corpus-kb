import { Button, Card, Typography } from "antd";
import { useRef, useState } from "react";
import { strings } from "../strings";
import { OPS } from "../api/ops";
import OpRunner from "../components/OpRunner";
import AdvisorCard from "./analyze/AdvisorCard";
import ActionCard from "../components/ActionCard";
import ClassificationCard from "./analyze/ClassificationCard";
import LabelingCard from "./analyze/LabelingCard";
import NotWiredCard from "../components/NotWiredCard";

/** The honest not-wired rows for the analysis families (op id, line). */
const NOT_WIRED = [
  { opId: "statements-list", text: strings.analyzeStatementsNotWired },
  { opId: "conflicts-scan", text: strings.analyzeConflictsNotWired },
  { opId: "beliefs-current", text: strings.analyzeBeliefsNotWired },
  { opId: "analysis-embed-meta", text: strings.analyzeEmbedMetaNotWired },
  { opId: "extraction-tune", text: strings.analyzeExtractionTuneNotWired },
];

/** The guided, present-in-manifest actions (dormant hints where known). */
const GUIDED = [
  {
    opId: "topics-build",
    title: strings.analyzeTopicsBuildTitle,
    body: strings.analyzeTopicsBuildBody,
  },
  {
    opId: "topics-trends",
    title: strings.analyzeTrendsTitle,
    body: strings.analyzeTrendsBody,
    dormant: {
      configKey: "analysis.topics.over_time.enabled",
      yaml: "analysis:\n  topics:\n    over_time:\n      enabled: true",
    },
  },
  {
    opId: "labeling-build",
    title: strings.analyzeLabelingTitle,
    body: strings.analyzeLabelingBody,
  },
  {
    opId: "classification-build",
    title: strings.analyzeClassificationTitle,
    body: strings.analyzeClassificationBody,
  },
  {
    opId: "analyze-run",
    title: strings.analyzeAnalyzeTitle,
    body: strings.analyzeAnalyzeBody,
  },
  {
    opId: "analysis-backfill-dates",
    title: strings.analyzeDatesTitle,
    body: strings.analyzeDatesBody,
  },
] as const;

/**
 * The advisor register's human name per manifest op (the register ranks
 * methods, and several methods share one op, so the picker names ops).
 * A manifest op missing here falls back to its raw id — never a blank.
 */
const METHOD_NAMES: Record<string, string> = {
  "active-learn-next": strings.analyzeOpActiveLearn,
  "analysis-backfill-dates": strings.analyzeOpBackfillDates,
  "centroids-classify": strings.analyzeOpCentroids,
  "clusters-inspect": strings.analyzeOpClusters,
  "contradictions-screen": strings.analyzeOpContradictions,
  "duplicates-screen": strings.analyzeOpDuplicates,
  "keyphrases-extract": strings.analyzeOpKeyphrases,
  "lexical-classify": strings.analyzeOpLexical,
  "neighbors-classify": strings.analyzeOpNeighbors,
  "nli-classify": strings.analyzeOpNli,
  "outliers-rank": strings.analyzeOpOutliers,
  "sentiment-screen": strings.analyzeOpSentiment,
  "topics-build": strings.analyzeOpTopics,
};

/**
 * The manifest's generic analysis methods: every /api/analysis/* op
 * except the advisor itself (spec F8's eleven methods). Derived, so a
 * manifest change updates the picker without a code edit.
 */
const METHODS: string[] = Object.entries(OPS)
  .filter(([id, op]) => op.path.startsWith("/api/analysis/") && id !== "analysis-select-method")
  .map(([id]) => id)
  .sort();

/**
 * The Analyze stage — the method advisor, the manifest-driven runner,
 * and the guided build actions. Dormant families show honest banners.
 */
export default function Analyze() {
  const [method, setMethod] = useState<string>("keyphrases-extract");
  const [seed, setSeed] = useState<unknown>({});
  // The runner heading takes focus on an advisor handoff, so the click
  // visibly lands on the card it drives (never a silent state swap).
  const runnerHeading = useRef<HTMLSpanElement | null>(null);
  return (
    <div className="ui-stack">
      <Card title={strings.analyzeTitle}>
        <p>{strings.analyzeBody}</p>
      </Card>
      <AdvisorCard
        onRunMethod={(opId) => {
          setMethod(opId);
          setSeed({});
          runnerHeading.current?.focus();
        }}
      />
      <Card
        title={
          <span ref={runnerHeading} tabIndex={-1}>
            {strings.analyzeRunnerTitle}
          </span>
        }
      >
        <Typography.Paragraph type="secondary">
          {strings.analyzeRunnerBody}
        </Typography.Paragraph>
        <div className="ui-actions">
          {METHODS.map((id) => (
            <Button
              key={id}
              size="small"
              title={id}
              aria-pressed={method === id}
              type={method === id ? "primary" : "default"}
              onClick={() => setMethod(id)}
            >
              {METHOD_NAMES[id] ?? id}
            </Button>
          ))}
        </div>
        {/* key remounts the runner on a method switch: no receipt or
            loading state from the previous op can leak into it. */}
        <OpRunner key={method} opId={method} initialBody={seed} />
      </Card>
      {GUIDED.map((card) => (
        <ActionCard key={card.opId} {...card} />
      ))}
      <LabelingCard />
      <ClassificationCard />
      <NotWiredCard title={strings.analyzeNotWiredTitle} items={NOT_WIRED} />
    </div>
  );
}
