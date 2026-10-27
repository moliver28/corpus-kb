import { Button, Card, Typography } from "antd";
import { useState } from "react";
import { strings } from "../strings";
import OpRunner from "../components/OpRunner";
import AdvisorCard from "./analyze/AdvisorCard";
import ActionCard from "./analyze/ActionCard";
import NotWiredCard from "./analyze/NotWiredCard";

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

/** The manifest's generic analysis methods, one picker button each. */
const METHODS = [
  "keyphrases-extract",
  "lexical-classify",
  "neighbors-classify",
  "centroids-classify",
  "clusters-inspect",
  "duplicates-screen",
  "outliers-rank",
  "nli-classify",
  "contradictions-screen",
  "sentiment-screen",
  "active-learn-next",
] as const;

/**
 * The Analyze stage — the method advisor, the manifest-driven runner,
 * and the guided build actions. Dormant families show honest banners.
 */
export default function Analyze() {
  const [method, setMethod] = useState<string>("keyphrases-extract");
  const [seed, setSeed] = useState<unknown>({});
  return (
    <div className="ui-stack">
      <Card title={strings.analyzeTitle}>
        <p>{strings.analyzeBody}</p>
      </Card>
      <AdvisorCard
        onRunMethod={(opId) => {
          setMethod(opId);
          setSeed({});
        }}
      />
      <Card title={strings.analyzeRunnerTitle}>
        <Typography.Paragraph type="secondary">
          {strings.analyzeRunnerBody}
        </Typography.Paragraph>
        <div className="ui-actions">
          {METHODS.map((id) => (
            <Button
              key={id}
              size="small"
              aria-pressed={method === id}
              type={method === id ? "primary" : "default"}
              onClick={() => setMethod(id)}
            >
              {id}
            </Button>
          ))}
        </div>
        <OpRunner opId={method} initialBody={seed} />
      </Card>
      {GUIDED.map((card) => (
        <ActionCard key={card.opId} {...card} />
      ))}
      <NotWiredCard />
    </div>
  );
}
